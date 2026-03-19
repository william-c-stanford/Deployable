"""JWT authentication with role-based scoping, token blacklisting, and demo mode."""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Set

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "deployable-dev-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

VALID_ROLES = {"ops", "technician", "partner"}

# Archetype labels per role — used in the JWT claims for UI display
ROLE_ARCHETYPES = {
    "ops": "Operations Manager",
    "technician": "Field Technician",
    "partner": "Partner Admin",
}

# ---------------------------------------------------------------------------
# In-memory token blacklist (production would use Redis)
# Stores JTI (JWT ID) strings for invalidated tokens
# ---------------------------------------------------------------------------
_blacklisted_jtis: Set[str] = set()


def blacklist_token(jti: str) -> None:
    """Add a JTI to the blacklist so the token is rejected on future requests."""
    _blacklisted_jtis.add(jti)


def is_blacklisted(jti: str) -> bool:
    """Check whether a JTI has been blacklisted."""
    return jti in _blacklisted_jtis


def clear_blacklist() -> None:
    """Clear the blacklist — useful for tests."""
    _blacklisted_jtis.clear()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CurrentUser(BaseModel):
    user_id: str
    role: str
    account_id: Optional[str] = None
    archetype: Optional[str] = None
    name: Optional[str] = None
    jti: Optional[str] = None


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    role: str,
    *,
    account_id: Optional[str] = None,
    name: Optional[str] = None,
    archetype: Optional[str] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """Create a signed JWT with role, account, archetype, and JTI claims.

    Args:
        user_id: The user's primary identifier (UUID string).
        role: One of ops, technician, partner.
        account_id: Scoped account ID (e.g. technician_id or partner_id).
        name: Human-readable display name.
        archetype: Display archetype label. Defaults to ROLE_ARCHETYPES[role].
        extra_claims: Additional claims to embed in the token.

    Returns:
        Encoded JWT string.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    jti = str(uuid.uuid4())
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "account_id": account_id,
        "name": name,
        "archetype": archetype or ROLE_ARCHETYPES.get(role, role),
        "jti": jti,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_demo_token(
    user_id: str,
    role: str,
    name: str | None = None,
    scoped_to: str | None = None,
) -> str:
    """Create a signed JWT for demo/testing purposes (backward compat)."""
    return create_access_token(
        user_id=user_id,
        role=role,
        name=name,
        account_id=scoped_to,
        extra_claims={"scoped_to": scoped_to, "user_id": user_id} if scoped_to else {"user_id": user_id},
    )


# ---------------------------------------------------------------------------
# Token decoding & user extraction
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        jti: str = payload.get("jti")

        if user_id is None or role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        # Check blacklist
        if jti and is_blacklisted(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been invalidated",
            )

        return CurrentUser(
            user_id=user_id,
            role=role,
            account_id=payload.get("account_id"),
            archetype=payload.get("archetype"),
            name=payload.get("name"),
            jti=jti,
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


def get_current_user(request: Request) -> CurrentUser:
    """Extract the current user from JWT or demo headers.

    For demo convenience, accepts X-Demo-Role (and optional X-Demo-User-Id)
    headers so the frontend can switch roles without real auth.
    """
    # Demo mode: accept X-Demo-Role header for easy role switching
    demo_role = request.headers.get("X-Demo-Role")
    if demo_role and demo_role in VALID_ROLES:
        demo_user_id = request.headers.get("X-Demo-User-Id", "demo-user")
        demo_account_id = request.headers.get("X-Demo-Account-Id")
        return CurrentUser(
            user_id=demo_user_id,
            role=demo_role,
            account_id=demo_account_id,
            archetype=ROLE_ARCHETYPES.get(demo_role, demo_role),
        )

    # Standard JWT auth
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = auth_header.removeprefix("Bearer ").strip()
    return _decode_token(token)


def extract_raw_token(request: Request) -> Optional[str]:
    """Extract the raw JWT string from the Authorization header, if present."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return None


def require_role(*allowed_roles: str):
    """Dependency factory that checks the user has one of the allowed roles."""

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' not permitted. Required: {allowed_roles}",
            )
        return current_user

    return _check
