"""Authentication routes."""
from __future__ import annotations

import bcrypt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, status

from observibot.api.deps import create_access_token, get_current_user, get_store
from observibot.api.schemas import LoginRequest, RegisterRequest, UserResponse
from observibot.core.models import _new_id
from observibot.core.store import Store, users_table

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _utcnow_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


@router.post("/register")
async def register(req: RegisterRequest, response: Response, store: Store = Depends(get_store)):
    """Register a new user. Only works if no users exist (first-run setup)."""
    async with store.engine.begin() as conn:
        result = await conn.execute(sa.select(sa.func.count()).select_from(users_table))
        count = result.scalar() or 0

    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled. A user already exists.",
        )

    user_id = _new_id()
    hashed = _hash_password(req.password)
    async with store.engine.begin() as conn:
        await conn.execute(
            users_table.insert().values(
                id=user_id,
                email=req.email,
                password_hash=hashed,
                is_admin=True,
                tenant_id=1,
                created_at=_utcnow_iso(),
            )
        )

    token = create_access_token({"sub": user_id, "email": req.email, "is_admin": True})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return UserResponse(id=user_id, email=req.email, is_admin=True)


@router.post("/login")
async def login(req: LoginRequest, response: Response, store: Store = Depends(get_store)):
    """Authenticate with email and password, receive JWT in httpOnly cookie."""
    async with store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                users_table.c.id,
                users_table.c.email,
                users_table.c.password_hash,
                users_table.c.is_admin,
            ).where(users_table.c.email == req.email)
        )
        user = result.fetchone()

    if user is None or not _verify_password(req.password, user[2]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token({"sub": user[0], "email": user[1], "is_admin": bool(user[3])})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return UserResponse(id=user[0], email=user[1], is_admin=bool(user[3]))


@router.post("/logout")
async def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie(key="access_token")
    return {"detail": "Logged out"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user."""
    return UserResponse(id=user["id"], email=user["email"], is_admin=user["is_admin"])
