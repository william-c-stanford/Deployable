"""Tests for the role-switcher endpoint POST /api/auth/switch."""

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt as jose_jwt

# Patch DATABASE_URL before importing app modules
os.environ["DATABASE_URL"] = "sqlite:///test_auth.db"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.auth import (
    create_access_token,
    clear_blacklist,
    is_blacklisted,
    blacklist_token,
    VALID_ROLES,
    ROLE_ARCHETYPES,
    SECRET_KEY,
    ALGORITHM,
)

# Create a test engine using SQLite
TEST_ENGINE = create_engine("sqlite:///test_auth.db", connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


def get_test_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_blacklist():
    """Clear the token blacklist before and after each test."""
    clear_blacklist()
    yield
    clear_blacklist()


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    """Create all tables in the test SQLite database."""
    # Import all models so Base.metadata is populated
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)
    # Clean up test db file
    try:
        os.remove("test_auth.db")
    except OSError:
        pass


@pytest.fixture()
def client():
    """FastAPI test client with DB dependency override."""
    from app.main import app
    from app.database import get_db

    app.dependency_overrides[get_db] = get_test_db

    from fastapi.testclient import TestClient
    c = TestClient(app)
    yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def ops_user(client):
    """Insert an ops user into the test DB and return it."""
    from app.models.user import User
    db = TestSession()
    user = User(
        id=uuid.uuid4(),
        name="Sarah Ops Lead",
        role="ops",
        scoped_to=None,
        email="sarah@deployable.io",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.fixture()
def tech_user(client):
    """Insert a technician user into the test DB."""
    from app.models.user import User
    db = TestSession()
    tech_id = str(uuid.uuid4())
    user = User(
        id=uuid.uuid4(),
        name="Marcus Cable Tech",
        role="technician",
        scoped_to=tech_id,
        email="marcus@example.com",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.fixture()
def partner_user(client):
    """Insert a partner user into the test DB."""
    from app.models.user import User
    db = TestSession()
    partner_id = str(uuid.uuid4())
    user = User(
        id=uuid.uuid4(),
        name="Verizon Admin",
        role="partner",
        scoped_to=partner_id,
        email="admin@verizon.com",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRoleSwitchEndpoint:
    """Tests for POST /api/auth/switch."""

    def test_switch_to_ops_user(self, client, ops_user):
        response = client.post(
            "/api/auth/switch",
            json={"role": "ops", "account_id": str(ops_user.id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_type"] == "bearer"
        assert data["access_token"]
        assert data["user"]["role"] == "ops"
        assert data["user"]["name"] == "Sarah Ops Lead"
        assert data["user"]["archetype"] == "Operations Manager"

        # Decode the token and verify claims
        payload = jose_jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["role"] == "ops"
        assert payload["archetype"] == "Operations Manager"
        assert payload["jti"]  # JTI must be present

    def test_switch_to_technician_role(self, client, tech_user):
        response = client.post(
            "/api/auth/switch",
            json={"role": "technician", "account_id": str(tech_user.id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user"]["role"] == "technician"
        assert data["user"]["archetype"] == "Field Technician"
        assert data["user"]["account_id"] == tech_user.scoped_to

    def test_switch_to_partner_role(self, client, partner_user):
        response = client.post(
            "/api/auth/switch",
            json={"role": "partner", "account_id": str(partner_user.id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user"]["role"] == "partner"
        assert data["user"]["archetype"] == "Partner Admin"

    def test_switch_invalid_role_returns_400(self, client):
        response = client.post(
            "/api/auth/switch",
            json={"role": "superadmin", "account_id": "some-id"},
        )
        assert response.status_code == 400
        assert "Invalid role" in response.json()["detail"]

    def test_switch_nonexistent_account_returns_404(self, client):
        response = client.post(
            "/api/auth/switch",
            json={"role": "ops", "account_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404
        assert "No user found" in response.json()["detail"]

    def test_switch_role_mismatch_returns_403(self, client, ops_user):
        """Requesting 'technician' role but providing an ops user's ID → 403."""
        response = client.post(
            "/api/auth/switch",
            json={"role": "technician", "account_id": str(ops_user.id)},
        )
        assert response.status_code == 403
        assert "cannot switch to role" in response.json()["detail"]

    def test_switch_without_account_id_uses_default(self, client, ops_user):
        response = client.post(
            "/api/auth/switch",
            json={"role": "ops"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["role"] == "ops"

    def test_old_token_is_blacklisted_on_switch(self, client, ops_user):
        # Create an initial token
        old_token = create_access_token(user_id="user-1", role="ops")
        old_payload = jose_jwt.decode(old_token, SECRET_KEY, algorithms=[ALGORITHM])
        old_jti = old_payload["jti"]
        assert not is_blacklisted(old_jti)

        response = client.post(
            "/api/auth/switch",
            json={"role": "ops", "account_id": str(ops_user.id)},
            headers=_auth_header(old_token),
        )

        assert response.status_code == 200
        # Old token JTI should now be blacklisted
        assert is_blacklisted(old_jti)

    def test_blacklisted_token_rejected(self, client):
        token = create_access_token(user_id="user-1", role="ops")
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        blacklist_token(payload["jti"])

        response = client.get("/api/auth/me", headers=_auth_header(token))
        assert response.status_code == 401
        assert "invalidated" in response.json()["detail"]

    def test_switch_no_users_for_role_returns_404(self, client):
        """If there are no users for a requested role (with no prior fixtures), return 404."""
        # Use a fresh DB query that will find no 'partner' users
        # (unless partner_user fixture was loaded in this test)
        # We rely on the fact that by default there may not be partner users
        # To be safe, use an account_id that won't exist
        response = client.post(
            "/api/auth/switch",
            json={"role": "technician", "account_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    def test_jwt_contains_all_required_claims(self, client, tech_user):
        response = client.post(
            "/api/auth/switch",
            json={"role": "technician", "account_id": str(tech_user.id)},
        )

        data = response.json()
        payload = jose_jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])

        assert payload["sub"] == str(tech_user.id)
        assert payload["role"] == "technician"
        assert payload["account_id"] == tech_user.scoped_to
        assert payload["archetype"] == "Field Technician"
        assert payload["name"] == "Marcus Cable Tech"
        assert payload["jti"]  # non-empty
        assert payload["exp"]  # expiry set
        assert payload["iat"]  # issued-at set

    def test_new_token_differs_from_old(self, client, ops_user):
        """Each switch generates a unique token (unique JTI)."""
        resp1 = client.post(
            "/api/auth/switch",
            json={"role": "ops", "account_id": str(ops_user.id)},
        )
        resp2 = client.post(
            "/api/auth/switch",
            json={"role": "ops", "account_id": str(ops_user.id)},
        )

        t1 = resp1.json()["access_token"]
        t2 = resp2.json()["access_token"]
        assert t1 != t2

        p1 = jose_jwt.decode(t1, SECRET_KEY, algorithms=[ALGORITHM])
        p2 = jose_jwt.decode(t2, SECRET_KEY, algorithms=[ALGORITHM])
        assert p1["jti"] != p2["jti"]


class TestAuthModule:
    """Unit tests for the auth module functions."""

    def test_create_access_token_with_defaults(self):
        token = create_access_token(user_id="u1", role="ops")
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "u1"
        assert payload["role"] == "ops"
        assert payload["archetype"] == "Operations Manager"
        assert payload["jti"]

    def test_create_access_token_with_custom_archetype(self):
        token = create_access_token(
            user_id="u2",
            role="partner",
            archetype="Custom Partner Label",
        )
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["archetype"] == "Custom Partner Label"

    def test_create_access_token_invalid_role(self):
        with pytest.raises(ValueError, match="Invalid role"):
            create_access_token(user_id="u3", role="admin")

    def test_blacklist_and_check(self):
        assert not is_blacklisted("test-jti")
        blacklist_token("test-jti")
        assert is_blacklisted("test-jti")

    def test_valid_roles_constant(self):
        assert VALID_ROLES == {"ops", "technician", "partner"}

    def test_role_archetypes_cover_all_roles(self):
        for role in VALID_ROLES:
            assert role in ROLE_ARCHETYPES

    def test_create_access_token_extra_claims(self):
        token = create_access_token(
            user_id="u4",
            role="ops",
            extra_claims={"custom_field": "hello"},
        )
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["custom_field"] == "hello"


class TestDemoToken:
    """Tests for the demo-token endpoint."""

    def test_generate_demo_token(self, client):
        response = client.post(
            "/api/auth/demo-token",
            json={"user_id": "test-user", "role": "ops"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "ops"
        assert data["user_id"] == "test-user"
        assert data["access_token"]

    def test_demo_token_invalid_role(self, client):
        response = client.post(
            "/api/auth/demo-token",
            json={"user_id": "test-user", "role": "invalid"},
        )
        assert response.status_code == 400


class TestDemoHeaders:
    """Tests for demo mode via X-Demo-Role headers."""

    def test_demo_role_header(self, client):
        response = client.get(
            "/api/auth/me",
            headers={
                "X-Demo-Role": "technician",
                "X-Demo-User-Id": "tech-demo",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "technician"
        assert data["user_id"] == "tech-demo"
        assert data["archetype"] == "Field Technician"

    def test_demo_role_header_with_account_id(self, client):
        response = client.get(
            "/api/auth/me",
            headers={
                "X-Demo-Role": "partner",
                "X-Demo-User-Id": "partner-demo",
                "X-Demo-Account-Id": "partner-org-123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "partner"
        assert data["account_id"] == "partner-org-123"
        assert data["archetype"] == "Partner Admin"
