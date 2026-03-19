"""Auth routes: role switching, demo token generation, and current-user info."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    create_demo_token,
    get_current_user,
    extract_raw_token,
    blacklist_token,
    CurrentUser,
    VALID_ROLES,
    ROLE_ARCHETYPES,
)
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RoleSwitchRequest(BaseModel):
    """Request body for switching roles.

    role: Target role (ops, technician, partner).
    account_id: Account to scope into (user UUID or scoped_to value).
                For technician/partner roles this is required.
    """
    role: str
    account_id: Optional[str] = None


class RoleSwitchResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class DemoTokenRequest(BaseModel):
    user_id: str = "demo-user"
    role: str = "ops"
    name: Optional[str] = None
    scoped_to: Optional[str] = None


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: str


class UserListItem(BaseModel):
    id: str
    name: str
    role: str
    scoped_to: Optional[str] = None
    archetype: str


# ---------------------------------------------------------------------------
# Role-switcher endpoint
# ---------------------------------------------------------------------------

@router.post("/switch", response_model=RoleSwitchResponse)
def switch_role(
    body: RoleSwitchRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Switch the current session to a different role and/or account.

    Validates:
    - The target role is a valid Deployable role.
    - The account_id resolves to a real User record with matching role.
    - Partners cannot access ops-only data (enforced at token level).

    Generates a fresh JWT with updated claims and blacklists the old token.
    """
    # --- Validate role ---
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{body.role}'. Must be one of: {sorted(VALID_ROLES)}",
        )

    # --- Resolve target user / account ---
    target_user: Optional[User] = None

    if body.account_id:
        # Try to find the user by ID (convert string → UUID) or by scoped_to field
        try:
            account_uuid = uuid.UUID(body.account_id)
            target_user = db.query(User).filter(
                User.id == account_uuid
            ).first()
        except (ValueError, AttributeError):
            target_user = None

        if target_user is None:
            # Fallback: try matching by scoped_to value
            target_user = db.query(User).filter(
                User.scoped_to == body.account_id,
                User.role == body.role,
            ).first()

        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No user found for account_id '{body.account_id}'",
            )

        # Ensure the resolved user's role matches the requested role
        if target_user.role != body.role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Account '{body.account_id}' has role '{target_user.role}', "
                    f"cannot switch to role '{body.role}'"
                ),
            )
    else:
        # No account_id provided — find a default user for the role
        target_user = db.query(User).filter(
            User.role == body.role,
        ).first()

        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No user with role '{body.role}' found in the system",
            )

    # --- Blacklist the old token (if present) ---
    old_token = extract_raw_token(request)
    if old_token:
        try:
            from jose import jwt as jose_jwt
            from app.auth import SECRET_KEY, ALGORITHM
            payload = jose_jwt.decode(old_token, SECRET_KEY, algorithms=[ALGORITHM])
            old_jti = payload.get("jti")
            if old_jti:
                blacklist_token(old_jti)
        except Exception:
            pass  # Old token may already be expired / invalid — that's fine

    # --- Build archetype label ---
    archetype = ROLE_ARCHETYPES.get(body.role, body.role)

    # --- Generate fresh JWT ---
    new_token = create_access_token(
        user_id=str(target_user.id),
        role=target_user.role,
        account_id=target_user.scoped_to or str(target_user.id),
        name=target_user.name,
        archetype=archetype,
    )

    user_info = {
        "user_id": str(target_user.id),
        "name": target_user.name,
        "role": target_user.role,
        "account_id": target_user.scoped_to or str(target_user.id),
        "archetype": archetype,
    }

    return RoleSwitchResponse(access_token=new_token, user=user_info)


# ---------------------------------------------------------------------------
# List available users for role switcher UI
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserListItem])
def list_users(db: Session = Depends(get_db)):
    """List all demo users available for the role switcher."""
    users = db.query(User).order_by(User.role, User.name).all()
    return [
        UserListItem(
            id=str(u.id),
            name=u.name,
            role=u.role,
            scoped_to=u.scoped_to,
            archetype=ROLE_ARCHETYPES.get(u.role, u.role),
        )
        for u in users
    ]


# ---------------------------------------------------------------------------
# Demo token generation (for quick testing)
# ---------------------------------------------------------------------------

@router.post("/demo-token", response_model=DemoTokenResponse)
def generate_demo_token(body: DemoTokenRequest):
    """Generate a demo JWT for quick testing. Accepts any role in: ops, technician, partner."""
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{body.role}'. Must be one of: {sorted(VALID_ROLES)}",
        )
    token = create_demo_token(
        user_id=body.user_id,
        role=body.role,
        name=body.name,
        scoped_to=body.scoped_to,
    )
    return DemoTokenResponse(
        access_token=token,
        role=body.role,
        user_id=body.user_id,
    )


# ---------------------------------------------------------------------------
# Current user info
# ---------------------------------------------------------------------------

@router.get("/me")
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Return the currently authenticated user info."""
    return current_user
