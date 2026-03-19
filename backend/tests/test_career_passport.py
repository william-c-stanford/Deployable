"""Tests for career passport token endpoints — generate, list, revoke, validate,
and public shareable URL route with token validation."""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.career_passport_token import CareerPassportToken
from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianBadge,
    DeployabilityStatus,
    CareerStage,
    ProficiencyLevel,
    CertStatus,
    BadgeType,
)

# ---------------------------------------------------------------------------
# In-memory SQLite test database
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# Common headers
OPS_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}
PARTNER_HEADERS = {"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-1"}

TECH_ID = uuid.uuid4()
TECH_HEADERS = {"X-Demo-Role": "technician", "X-Demo-User-Id": str(TECH_ID)}
OTHER_TECH_HEADERS = {"X-Demo-Role": "technician", "X-Demo-User-Id": str(uuid.uuid4())}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test and drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def technician():
    """Create a sample technician."""
    db = TestingSessionLocal()
    tech = Technician(
        id=TECH_ID,
        first_name="Alice",
        last_name="Johnson",
        email="alice@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.READY_NOW,
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    db.close()
    return tech


# ---------------------------------------------------------------------------
# Token Generation Tests
# ---------------------------------------------------------------------------

class TestGenerateToken:
    def test_ops_can_generate_token(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["technician_id"] == str(TECH_ID)
        assert data["revoked"] is False
        assert data["is_active"] is True
        assert data["created_by_role"] == "ops"
        assert data["share_url"].startswith("/passport/")
        assert data["token"]
        assert data["expires_at"]

    def test_technician_can_generate_own_token(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created_by_role"] == "technician"

    def test_technician_cannot_generate_token_for_other(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OTHER_TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_partner_cannot_generate_token(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=PARTNER_HEADERS,
        )
        assert resp.status_code == 403

    def test_custom_expiry_days(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID), "expiry_days": 7},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        expires = datetime.fromisoformat(data["expires_at"])
        # Should expire roughly 7 days from now
        expected = datetime.utcnow() + timedelta(days=7)
        assert abs((expires - expected).total_seconds()) < 60

    def test_custom_label(self, technician):
        resp = client.post(
            "/api/career-passport/tokens",
            json={
                "technician_id": str(TECH_ID),
                "label": "For Acme Corp interview",
            },
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["label"] == "For Acme Corp interview"

    def test_nonexistent_technician(self):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": fake_id},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Token Listing Tests
# ---------------------------------------------------------------------------

class TestListTokens:
    def test_ops_list_tokens(self, technician):
        # Generate two tokens
        client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID), "label": "Token 1"},
            headers=OPS_HEADERS,
        )
        client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID), "label": "Token 2"},
            headers=OPS_HEADERS,
        )
        resp = client.get(
            f"/api/career-passport/tokens/technician/{TECH_ID}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["tokens"]) == 2

    def test_technician_list_own_tokens(self, technician):
        client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=TECH_HEADERS,
        )
        resp = client.get(
            f"/api/career-passport/tokens/technician/{TECH_ID}",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_technician_cannot_list_other_tokens(self, technician):
        resp = client.get(
            f"/api/career-passport/tokens/technician/{TECH_ID}",
            headers=OTHER_TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_list_excludes_revoked_by_default(self, technician):
        # Create and revoke a token
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_id = create_resp.json()["id"]
        client.post(
            f"/api/career-passport/tokens/{token_id}/revoke",
            headers=OPS_HEADERS,
        )

        # Default list should be empty
        resp = client.get(
            f"/api/career-passport/tokens/technician/{TECH_ID}",
            headers=OPS_HEADERS,
        )
        assert resp.json()["count"] == 0

        # Include revoked
        resp = client.get(
            f"/api/career-passport/tokens/technician/{TECH_ID}?include_revoked=true",
            headers=OPS_HEADERS,
        )
        assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# Token Revocation Tests
# ---------------------------------------------------------------------------

class TestRevokeToken:
    def test_ops_revoke_token(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/career-passport/tokens/{token_id}/revoke",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["revoked"] is True
        assert data["revoked_at"] is not None

    def test_technician_revoke_own_token(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=TECH_HEADERS,
        )
        token_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/career-passport/tokens/{token_id}/revoke",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 200

    def test_technician_cannot_revoke_other_token(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/career-passport/tokens/{token_id}/revoke",
            headers=OTHER_TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_double_revoke_returns_conflict(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_id = create_resp.json()["id"]

        client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)
        resp = client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)
        assert resp.status_code == 409

    def test_revoke_nonexistent_token(self, technician):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/career-passport/tokens/{fake_id}/revoke",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Token Validation Tests (public endpoint)
# ---------------------------------------------------------------------------

class TestValidateToken:
    def test_validate_active_token(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = create_resp.json()["token"]

        resp = client.get(f"/api/career-passport/validate/{token_value}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["technician_id"] == str(TECH_ID)

    def test_validate_revoked_token(self, technician):
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_id = create_resp.json()["id"]
        token_value = create_resp.json()["token"]

        client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)

        resp = client.get(f"/api/career-passport/validate/{token_value}")
        assert resp.status_code == 410

    def test_validate_expired_token(self, technician):
        # Directly create an expired token in DB
        db = TestingSessionLocal()
        token = CareerPassportToken(
            technician_id=TECH_ID,
            created_by_user_id="ops-user-1",
            created_by_role="ops",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        token_value = token.token
        db.close()

        resp = client.get(f"/api/career-passport/validate/{token_value}")
        assert resp.status_code == 410

    def test_validate_unknown_token(self):
        resp = client.get("/api/career-passport/validate/totally-invalid-token")
        assert resp.status_code == 404

    def test_validate_no_auth_required(self, technician):
        """Validate endpoint should work without any auth headers."""
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = create_resp.json()["token"]

        # No headers at all
        resp = client.get(f"/api/career-passport/validate/{token_value}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Public Career Passport HTML View Tests
# ---------------------------------------------------------------------------

class TestPublicPassportHTML:
    """Tests for the unauthenticated public HTML career passport endpoint."""

    def _create_token(self, technician) -> str:
        """Helper to create a token and return the token value."""
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        return resp.json()["token"]

    def test_public_html_renders_with_valid_token(self, technician):
        """Valid token renders a full HTML page with technician data."""
        token_value = self._create_token(technician)

        resp = client.get(
            f"/api/career-passport/public/{token_value}",
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Should contain technician name
        assert "Alice" in resp.text
        assert "Johnson" in resp.text
        # Should contain Deployable branding
        assert "Deployable" in resp.text
        assert "Career Passport" in resp.text

    def test_public_html_no_auth_required(self, technician):
        """HTML endpoint works without any auth headers."""
        token_value = self._create_token(technician)

        # No headers at all — should still work
        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 200
        assert "Alice" in resp.text

    def test_public_html_invalid_token_shows_error(self):
        """Invalid token shows branded error page, not 500."""
        resp = client.get("/api/career-passport/public/totally-bogus-token-value")
        assert resp.status_code == 404
        assert "text/html" in resp.headers["content-type"]
        assert "Not Found" in resp.text

    def test_public_html_revoked_token_shows_error(self, technician):
        """Revoked token shows branded error page."""
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = create_resp.json()["token"]
        token_id = create_resp.json()["id"]

        # Revoke
        client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)

        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 410
        assert "text/html" in resp.headers["content-type"]
        assert "Revoked" in resp.text

    def test_public_html_expired_token_shows_error(self, technician):
        """Expired token shows branded error page."""
        db = TestingSessionLocal()
        token = CareerPassportToken(
            technician_id=TECH_ID,
            created_by_user_id="ops-user-1",
            created_by_role="ops",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        token_value = token.token
        db.close()

        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 410
        assert "text/html" in resp.headers["content-type"]
        assert "Expired" in resp.text

    def test_public_html_shows_skills(self, technician):
        """Public page includes technician skills."""
        db = TestingSessionLocal()
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.ADVANCED,
        ))
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="OTDR Testing",
            proficiency_level=ProficiencyLevel.INTERMEDIATE,
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 200
        assert "Fiber Splicing" in resp.text
        assert "OTDR Testing" in resp.text
        assert "Advanced" in resp.text

    def test_public_html_shows_certifications(self, technician):
        """Public page includes certifications."""
        db = TestingSessionLocal()
        db.add(TechnicianCertification(
            technician_id=TECH_ID,
            cert_name="FOA CFOT",
            status=CertStatus.ACTIVE,
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 200
        assert "FOA CFOT" in resp.text
        assert "Active" in resp.text

    def test_public_html_shows_badges(self, technician):
        """Public page includes badges."""
        db = TestingSessionLocal()
        db.add(TechnicianBadge(
            technician_id=TECH_ID,
            badge_name="100 Projects",
            badge_type=BadgeType.MILESTONE,
            description="Completed 100 projects",
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}")
        assert resp.status_code == 200
        assert "100 Projects" in resp.text


# ---------------------------------------------------------------------------
# Public Career Passport JSON View Tests
# ---------------------------------------------------------------------------

class TestPublicPassportJSON:
    """Tests for the unauthenticated public JSON career passport endpoint."""

    def _create_token(self, technician) -> str:
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        return resp.json()["token"]

    def test_public_json_valid_token(self, technician):
        """Valid token returns structured JSON with technician data."""
        token_value = self._create_token(technician)

        resp = client.get(f"/api/career-passport/public/{token_value}/json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["first_name"] == "Alice"
        assert data["last_name"] == "Johnson"
        assert data["career_stage"] == "Deployed"
        assert data["deployability_status"] == "Ready Now"
        assert isinstance(data["skills"], list)
        assert isinstance(data["certifications"], list)
        assert isinstance(data["badges"], list)
        assert isinstance(data["training_enrollments"], list)
        assert "token_expires_at" in data

    def test_public_json_no_auth_required(self, technician):
        """JSON endpoint works without any auth headers."""
        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}/json")
        assert resp.status_code == 200

    def test_public_json_invalid_token(self):
        """Invalid token returns 404."""
        resp = client.get("/api/career-passport/public/bogus-token/json")
        assert resp.status_code == 404

    def test_public_json_revoked_token(self, technician):
        """Revoked token returns 410."""
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = create_resp.json()["token"]
        token_id = create_resp.json()["id"]

        client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)

        resp = client.get(f"/api/career-passport/public/{token_value}/json")
        assert resp.status_code == 410

    def test_public_json_includes_skills(self, technician):
        """JSON response includes technician skills."""
        db = TestingSessionLocal()
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.ADVANCED,
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}/json")
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["skill_name"] == "Fiber Splicing"
        assert data["skills"][0]["proficiency_level"] == "Advanced"

    def test_public_json_active_cert_count(self, technician):
        """JSON response correctly counts active certifications."""
        db = TestingSessionLocal()
        db.add(TechnicianCertification(
            technician_id=TECH_ID,
            cert_name="FOA CFOT",
            status=CertStatus.ACTIVE,
        ))
        db.add(TechnicianCertification(
            technician_id=TECH_ID,
            cert_name="OSHA 30",
            status=CertStatus.EXPIRED,
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}/json")
        data = resp.json()
        assert data["active_cert_count"] == 1
        assert len(data["certifications"]) == 2


# ---------------------------------------------------------------------------
# Shortlink redirect tests
# ---------------------------------------------------------------------------

class TestPassportShortlink:
    """Tests for the /passport/{token} convenience redirect."""

    def test_shortlink_redirects_to_public_endpoint(self, technician):
        """The /passport/{token} route redirects to /api/career-passport/public/{token}."""
        resp_create = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = resp_create.json()["token"]

        # follow_redirects=False to verify the redirect itself
        resp = client.get(f"/passport/{token_value}", follow_redirects=False)
        assert resp.status_code == 302
        assert f"/api/career-passport/public/{token_value}" in resp.headers["location"]

    def test_shortlink_followed_renders_html(self, technician):
        """Following the redirect renders the full HTML page."""
        resp_create = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = resp_create.json()["token"]

        # follow_redirects=True (default) to verify full flow
        resp = client.get(f"/passport/{token_value}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Alice" in resp.text


# ---------------------------------------------------------------------------
# PDF Export Tests (authenticated)
# ---------------------------------------------------------------------------

class TestPassportPDF:
    """Tests for the career passport PDF generation endpoint."""

    def test_ops_can_download_pdf(self, technician):
        """Ops user can download any technician's career passport PDF."""
        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "attachment" in resp.headers["content-disposition"]
        assert "Alice_Johnson" in resp.headers["content-disposition"]
        # Should be valid PDF (starts with %PDF)
        assert resp.content[:5] == b"%PDF-"

    def test_technician_can_download_own_pdf(self, technician):
        """Technician can download their own career passport PDF."""
        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"

    def test_technician_cannot_download_other_pdf(self, technician):
        """Technician cannot download another technician's PDF."""
        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=OTHER_TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_partner_cannot_download_pdf(self, technician):
        """Partner users cannot access the authenticated PDF endpoint."""
        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=PARTNER_HEADERS,
        )
        assert resp.status_code == 403

    def test_pdf_nonexistent_technician(self):
        """PDF for nonexistent technician returns 404."""
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/career-passport/pdf/{fake_id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404

    def test_pdf_includes_skills_data(self, technician):
        """PDF is generated even when technician has skills and certs."""
        db = TestingSessionLocal()
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.ADVANCED,
        ))
        db.add(TechnicianCertification(
            technician_id=TECH_ID,
            cert_name="FOA CFOT",
            status=CertStatus.ACTIVE,
        ))
        db.add(TechnicianBadge(
            technician_id=TECH_ID,
            badge_name="100 Projects",
            badge_type=BadgeType.MILESTONE,
        ))
        db.commit()
        db.close()

        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert len(resp.content) > 100  # Non-trivial PDF size

    def test_pdf_content_length_header(self, technician):
        """PDF response includes Content-Length header."""
        resp = client.get(
            f"/api/career-passport/pdf/{TECH_ID}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        assert "content-length" in resp.headers
        assert int(resp.headers["content-length"]) > 0

    def test_unauthenticated_pdf_rejected(self, technician):
        """PDF endpoint requires authentication."""
        resp = client.get(f"/api/career-passport/pdf/{TECH_ID}")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Public PDF Export Tests (via share token)
# ---------------------------------------------------------------------------

class TestPublicPassportPDF:
    """Tests for the public career passport PDF endpoint (via share token)."""

    def _create_token(self, technician) -> str:
        resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        return resp.json()["token"]

    def test_public_pdf_with_valid_token(self, technician):
        """Valid share token allows public PDF download."""
        token_value = self._create_token(technician)

        resp = client.get(f"/api/career-passport/public/{token_value}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"

    def test_public_pdf_no_auth_required(self, technician):
        """Public PDF endpoint works without auth headers."""
        token_value = self._create_token(technician)

        # No auth headers at all
        resp = client.get(f"/api/career-passport/public/{token_value}/pdf")
        assert resp.status_code == 200

    def test_public_pdf_invalid_token(self):
        """Invalid token returns 404 for PDF endpoint."""
        resp = client.get("/api/career-passport/public/bogus-token/pdf")
        assert resp.status_code == 404

    def test_public_pdf_revoked_token(self, technician):
        """Revoked token returns 410 for PDF endpoint."""
        create_resp = client.post(
            "/api/career-passport/tokens",
            json={"technician_id": str(TECH_ID)},
            headers=OPS_HEADERS,
        )
        token_value = create_resp.json()["token"]
        token_id = create_resp.json()["id"]

        client.post(f"/api/career-passport/tokens/{token_id}/revoke", headers=OPS_HEADERS)

        resp = client.get(f"/api/career-passport/public/{token_value}/pdf")
        assert resp.status_code == 410

    def test_public_pdf_expired_token(self, technician):
        """Expired token returns 410 for PDF endpoint."""
        db = TestingSessionLocal()
        token = CareerPassportToken(
            technician_id=TECH_ID,
            created_by_user_id="ops-user-1",
            created_by_role="ops",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        token_value = token.token
        db.close()

        resp = client.get(f"/api/career-passport/public/{token_value}/pdf")
        assert resp.status_code == 410

    def test_public_pdf_with_full_data(self, technician):
        """Public PDF includes data when technician has skills, certs, badges."""
        db = TestingSessionLocal()
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.ADVANCED,
        ))
        db.add(TechnicianCertification(
            technician_id=TECH_ID,
            cert_name="FOA CFOT",
            status=CertStatus.ACTIVE,
        ))
        db.add(TechnicianBadge(
            technician_id=TECH_ID,
            badge_name="Top Performer",
            badge_type=BadgeType.CLIENT,
        ))
        db.commit()
        db.close()

        token_value = self._create_token(technician)
        resp = client.get(f"/api/career-passport/public/{token_value}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert len(resp.content) > 100


# ---------------------------------------------------------------------------
# Career Passport Service Unit Tests
# ---------------------------------------------------------------------------

class TestCareerPassportService:
    """Unit tests for the career passport service functions."""

    def test_compile_passport_data_returns_none_for_missing_tech(self):
        """compile_passport_data returns None for nonexistent technician."""
        from app.services.career_passport import compile_passport_data

        db = TestingSessionLocal()
        result = compile_passport_data(db, uuid.uuid4())
        db.close()
        assert result is None

    def test_compile_passport_data_returns_dict_for_valid_tech(self, technician):
        """compile_passport_data returns a dict with expected keys."""
        from app.services.career_passport import compile_passport_data

        db = TestingSessionLocal()
        result = compile_passport_data(db, TECH_ID)
        db.close()

        assert result is not None
        assert result["technician"].first_name == "Alice"
        assert result["technician"].last_name == "Johnson"
        assert isinstance(result["skills"], list)
        assert isinstance(result["certifications"], list)
        assert isinstance(result["badges"], list)
        assert isinstance(result["work_history"], list)
        assert isinstance(result["enrollments"], list)
        assert result["active_cert_count"] == 0
        assert result["generated_at"] is not None

    def test_compile_passport_data_sorts_skills(self, technician):
        """Skills are sorted by proficiency level (Advanced first)."""
        from app.services.career_passport import compile_passport_data

        db = TestingSessionLocal()
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Basic Skill",
            proficiency_level=ProficiencyLevel.APPRENTICE,
        ))
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Advanced Skill",
            proficiency_level=ProficiencyLevel.ADVANCED,
        ))
        db.add(TechnicianSkill(
            technician_id=TECH_ID,
            skill_name="Mid Skill",
            proficiency_level=ProficiencyLevel.INTERMEDIATE,
        ))
        db.commit()

        result = compile_passport_data(db, TECH_ID)
        db.close()

        assert result["skills"][0].skill_name == "Advanced Skill"
        assert result["skills"][1].skill_name == "Mid Skill"
        assert result["skills"][2].skill_name == "Basic Skill"

    def test_compile_passport_data_counts_active_certs(self, technician):
        """Active cert count is correctly calculated."""
        from app.services.career_passport import compile_passport_data

        db = TestingSessionLocal()
        db.add(TechnicianCertification(
            technician_id=TECH_ID, cert_name="Cert A", status=CertStatus.ACTIVE,
        ))
        db.add(TechnicianCertification(
            technician_id=TECH_ID, cert_name="Cert B", status=CertStatus.ACTIVE,
        ))
        db.add(TechnicianCertification(
            technician_id=TECH_ID, cert_name="Cert C", status=CertStatus.EXPIRED,
        ))
        db.commit()

        result = compile_passport_data(db, TECH_ID)
        db.close()

        assert result["active_cert_count"] == 2
        assert len(result["certifications"]) == 3

    def test_generate_passport_pdf_returns_bytes(self, technician):
        """generate_passport_pdf returns (bytes, filename) tuple."""
        from app.services.career_passport import generate_passport_pdf

        db = TestingSessionLocal()
        result = generate_passport_pdf(db, TECH_ID)
        db.close()

        assert result is not None
        pdf_bytes, filename = result
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert filename.endswith(".pdf")
        assert "Alice_Johnson" in filename

    def test_generate_passport_pdf_returns_none_for_missing_tech(self):
        """generate_passport_pdf returns None for nonexistent technician."""
        from app.services.career_passport import generate_passport_pdf

        db = TestingSessionLocal()
        result = generate_passport_pdf(db, uuid.uuid4())
        db.close()
        assert result is None
