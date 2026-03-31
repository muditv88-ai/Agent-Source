from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.user_store import authenticate, create_user, list_users, delete_user, update_password
from app.services.auth_service import create_access_token, get_current_user, require_admin

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    new_password: str


# ── Public ───────────────────────────────────────────────────────────────────

@router.post("/login")
def login(req: LoginRequest):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(user["username"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
    }


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return current_user


# ── Admin only ─────────────────────────────────────────────────────────────

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
    # Users can only change their own password; admins can change anyone's
    if current_user["username"] != username and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    try:
        update_password(username, req.new_password)
        return {"updated": username}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
