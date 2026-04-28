import os
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User, RefreshToken
import secrets

JWT_SECRET = os.getenv("JWT_SECRET", "changeme")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_MINUTES = 60

bearer_scheme = HTTPBearer()


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)


async def save_refresh_token(user_id: str, token: str):
    async with AsyncSessionLocal() as session:
        expires = datetime.now(timezone.utc) + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
        refresh = RefreshToken(
            user_id=user_id,
            token=token,
            expires_at=expires
        )
        session.add(refresh)
        await session.commit()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail={
                "status": "error",
                "message": "Invalid token type"
            })
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail={
            "status": "error",
            "message": "Invalid or expired token"
        })

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail={
            "status": "error",
            "message": "User not found"
        })
    if not user.is_active:
        raise HTTPException(status_code=403, detail={
            "status": "error",
            "message": "Account is deactivated"
        })
    return user


def require_role(required_role: str):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role != required_role and current_user.role != "admin":
            raise HTTPException(status_code=403, detail={
                "status": "error",
                "message": "Insufficient permissions"
            })
        return current_user
    return role_checker


# Convenience dependencies
require_admin = require_role("admin")
require_analyst = require_role("analyst")