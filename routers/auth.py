import os
import secrets as py_secrets
import httpx
from fastapi import APIRouter, HTTPException, Request, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from datetime import datetime, timezone
from core.limiter import limiter
from database import AsyncSessionLocal
from models import User, RefreshToken
from auth import (
    create_access_token, create_refresh_token,
    save_refresh_token, get_current_user
)
from schemas import RefreshRequest
import base64
import json as pyjson
from urllib.parse import urlencode


router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
# FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


GITHUB_WEB_CLIENT_ID = os.getenv("GITHUB_WEB_CLIENT_ID")
GITHUB_WEB_CLIENT_SECRET = os.getenv("GITHUB_WEB_CLIENT_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.get("/github")
@limiter.limit("10/minute")
async def github_login(request: Request):
    state = request.query_params.get("state", py_secrets.token_urlsafe(16))
    cli_callback = request.query_params.get("cli_callback", "")

    # ✅ encode cli_callback into state so we get it back from GitHub
    state_data = base64.urlsafe_b64encode(
        pyjson.dumps({"state": state, "cli_callback": cli_callback}).encode()
    ).decode()

    params = urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "scope": "user:email",
        "state": state_data,
    })
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")

@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(code: str, state: str, request: Request):
    # ✅ decode state to get original state + cli_callback
    try:
        state_data = pyjson.loads(base64.urlsafe_b64decode(state.encode()).decode())
        original_state = state_data.get("state", "")
        cli_callback = state_data.get("cli_callback", "")
    except Exception:
        original_state = state
        cli_callback = ""

    # Exchange code for GitHub token
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"}
        )
        token_data = token_res.json()

    github_token = token_data.get("access_token")
    if not github_token:
        raise HTTPException(status_code=502, detail={
            "status": "error",
            "message": "Failed to obtain GitHub access token"
        })

    # Fetch GitHub user
    async with httpx.AsyncClient() as client:
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {github_token}"}
        )
        github_user = user_res.json()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.github_id == str(github_user["id"]))
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                github_id=str(github_user["id"]),
                username=github_user.get("login"),
                email=github_user.get("email"),
                avatar_url=github_user.get("avatar_url"),
                role="analyst"
            )
            session.add(user)

        user.last_login_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(user)

    access_token = create_access_token(str(user.id), user.role)
    refresh_token = create_refresh_token()
    await save_refresh_token(str(user.id), refresh_token)

    # ✅ If CLI flow — redirect tokens back to local server
    if cli_callback:
        params = urlencode({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "username": user.username,
            "role": user.role,
            "state": original_state,
        })
        return RedirectResponse(f"{cli_callback}?{params}")

    # Browser flow — return JSON
    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
        }
    }

@router.post("/refresh")
async def refresh_token(request: Request, body: RefreshRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.token == body.refresh_token)
        )
        stored = result.scalar_one_or_none()

        if not stored or stored.is_revoked:
            raise HTTPException(status_code=401, detail={
                "status": "error",
                "message": "Invalid refresh token"
            })

        if stored.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail={
                "status": "error",
                "message": "Refresh token expired"
            })

        # Invalidate old token immediately
        stored.is_revoked = True
        await session.commit()

        # Fetch user
        user_result = await session.execute(
            select(User).where(User.id == stored.user_id)
        )
        user = user_result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail={
            "status": "error",
            "message": "User not found or deactivated"
        })

    # Issue new pair
    new_access = create_access_token(str(user.id), user.role)
    new_refresh = create_refresh_token()
    await save_refresh_token(str(user.id), new_refresh)

    return {
        "status": "success",
        "access_token": new_access,
        "refresh_token": new_refresh
    }


@router.post("/logout")
async def logout(request: Request, current_user: User = Depends(get_current_user), body: RefreshRequest = None):
    if body and body.refresh_token:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RefreshToken).where(RefreshToken.token == body.refresh_token)
            )
            stored = result.scalar_one_or_none()
            if stored:
                stored.is_revoked = True
                await session.commit()

    return {"status": "success", "message": "Logged out successfully"}


@router.get("/me")
async def get_me(request: Request, current_user: User = Depends(get_current_user)):
    return {
        "status": "success",
        "data": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
            "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None
        }
    }

# Web auth

@router.get("/web/github")
@limiter.limit("10/minute")
async def web_github_login(request: Request, response: Response):
    state = py_secrets.token_urlsafe(16)

    # Store state in HTTP-only cookie for CSRF validation
    response = RedirectResponse(
        f"https://github.com/login/oauth/authorize?"
        f"client_id={GITHUB_WEB_CLIENT_ID}&scope=user:email&state={state}"
    )
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        max_age=300  # 5 minutes
    )
    return response


@router.get("/web/github/callback")
@limiter.limit("10/minute")
async def web_github_callback(code: str, state: str, request: Request):
    # CSRF check — compare state with cookie
    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=403, detail={
            "status": "error",
            "message": "Invalid state parameter"
        })

    # Exchange code
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_WEB_CLIENT_ID,
                "client_secret": GITHUB_WEB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"}
        )
        token_data = token_res.json()

    github_token = token_data.get("access_token")
    if not github_token:
        raise HTTPException(status_code=502, detail={
            "status": "error",
            "message": "Failed to obtain GitHub access token"
        })

    # Fetch GitHub user
    async with httpx.AsyncClient() as client:
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {github_token}"}
        )
        github_user = user_res.json()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.github_id == str(github_user["id"]))
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                github_id=str(github_user["id"]),
                username=github_user.get("login"),
                email=github_user.get("email"),
                avatar_url=github_user.get("avatar_url"),
                role="analyst"
            )
            session.add(user)

        user.last_login_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(user)

    access_token = create_access_token(str(user.id), user.role)
    refresh_token = create_refresh_token()
    await save_refresh_token(str(user.id), refresh_token)

    # Set tokens in HTTP-only cookies — redirect to SPA root, React Router handles the rest
    response = RedirectResponse(url=f"{FRONTEND_URL}/")
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=180        # 3 minutes
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        samesite="lax",
        max_age=300        # 5 minutes
    )
    response.delete_cookie("oauth_state")
    return response


@router.post("/web/refresh")
async def web_refresh(request: Request):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail={
            "status": "error",
            "message": "No refresh token"
        })

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.token == refresh_token)
        )
        stored = result.scalar_one_or_none()

        if not stored or stored.is_revoked:
            raise HTTPException(status_code=401, detail={
                "status": "error",
                "message": "Invalid refresh token"
            })
        if stored.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail={
                "status": "error",
                "message": "Refresh token expired"
            })

        stored.is_revoked = True
        await session.commit()

        user_result = await session.execute(
            select(User).where(User.id == stored.user_id)
        )
        user = user_result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail={
            "status": "error",
            "message": "User not found or deactivated"
        })

    new_access = create_access_token(str(user.id), user.role)
    new_refresh = create_refresh_token()
    await save_refresh_token(str(user.id), new_refresh)

    response = JSONResponse(content={"status": "success"})
    response.set_cookie(key="access_token", value=new_access,
                        httponly=True, samesite="lax", max_age=180)
    response.set_cookie(key="refresh_token", value=new_refresh,
                        httponly=True, samesite="lax", max_age=300)
    return response


@router.post("/web/logout")
async def web_logout(request: Request):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RefreshToken).where(RefreshToken.token == refresh_token)
            )
            stored = result.scalar_one_or_none()
            if stored:
                stored.is_revoked = True
                await session.commit()

    response = JSONResponse(content={"status": "success"})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response


@router.get("/web/me")
async def web_me(current_user: User = Depends(get_current_user)):
    return {
        "status": "success",
        "data": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
            "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
        }
    }