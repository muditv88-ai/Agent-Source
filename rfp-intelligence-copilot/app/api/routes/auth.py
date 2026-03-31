import os
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from app.services.user_store import (
    authenticate, create_user, list_users,
    delete_user, update_password, get_user, get_or_create_google_user
)
from app.services.auth_service import create_access_token, get_current_user, require_admin

router = APIRouter()

ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "true").lower() == "true"


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class GoogleRequest(BaseModel):
    access_token: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    new_password: str


def _token_response(user: dict) -> dict:
    token = create_access_token(user["username"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
    }


# ── Public ───────────────────────────────────────────────────────────────────

@router.post("/login")
def login(req: LoginRequest):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return _token_response(user)


@router.post("/register", status_code=201)
def register(req: RegisterRequest):
    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Self-registration is disabled. Contact your administrator.")
    if len(req.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    try:
        user = create_user(req.username.strip(), req.password, role="user", email=req.email.strip())
        return _token_response(user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/google")
def google_login(req: GoogleRequest):
    """Exchange a Google access token for a ProcureIQ JWT."""
    # Fetch Google user info using the access token
    try:
        r = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {req.access_token}"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception:
        raise HTTPException(status_code=401, detail="Could not verify Google token")

    info = r.json()
    google_email = info.get("email")
    google_name  = info.get("name", "") or info.get("given_name", "")

    if not google_email:
        raise HTTPException(status_code=401, detail="Google account has no email")

    # Auto-create user on first login, or return existing
    user = get_or_create_google_user(google_email, google_name)
    return _token_response(user)


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return current_user


# ── Admin only ──────────────────────────────────────────────────────────────

@router.get("/users", dependencies=[Depends(require_admin)])
def list_all_users():
    return {"users": list_users()}


@router.post("/users", dependencies=[Depends(require_admin)], status_code=201)
def create_new_user(req: CreateUserRequest):
    try:
        user = create_user(req.username, req.password, req.role)
        return user
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/users/{username}", dependencies=[Depends(require_admin)])
def delete_user_endpoint(username: str, current_user: dict = Depends(require_admin)):
    if username == current_user["username"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    try:
        delete_user(username)
        return {"deleted": username}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/users/{username}/password")
def change_password(
    username: str,
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    if current_user["username"] != username and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    try:
        update_password(username, req.new_password)
        return {"updated": username}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
