from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.core.database import get_db
from backend.app.core.auth import create_access_token, get_current_user
from backend.app.models.models import User
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SwitchRoleRequest(BaseModel):
    user_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/switch", response_model=TokenResponse)
async def switch_role(
    request: SwitchRoleRequest,
    db: AsyncSession = Depends(get_db),
):
    """Switch role by selecting a user - generates new JWT token."""
    result = await db.execute(select(User).where(User.id == request.user_id))
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")

    token_data = {
        "user_id": user.id,
        "role": user.role,
        "scoped_to": user.scoped_to,
        "name": user.name,
    }
    token = create_access_token(token_data)
    return TokenResponse(access_token=token, user=token_data)


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    """List all demo users for role switcher."""
    result = await db.execute(select(User).order_by(User.role, User.name))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "role": u.role,
            "scoped_to": u.scoped_to,
        }
        for u in users
    ]


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user info from JWT."""
    return user
