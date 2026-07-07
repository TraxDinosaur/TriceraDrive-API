from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import AsyncStorage, get_storage

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SetupRequest(BaseModel):
    password: str


class SetupResponse(BaseModel):
    message: str


class StatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool = False


@router.get("/status", response_model=StatusResponse)
async def auth_status(storage: AsyncStorage = Depends(get_storage)):
    existing = await storage.get_setting("auth_salt")
    return StatusResponse(setup_required=existing is None)


@router.post("/setup", response_model=SetupResponse)
async def setup(body: SetupRequest, storage: AsyncStorage = Depends(get_storage)):
    if len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    existing = await storage.get_setting("auth_salt")
    if existing:
        raise HTTPException(status_code=400, detail="Already set up. Login instead.")

    salt_hex, key_hex = hash_password(body.password)
    await storage.set_setting("auth_salt", salt_hex)
    await storage.set_setting("auth_key", key_hex)
    return SetupResponse(message="Password set successfully")


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, storage: AsyncStorage = Depends(get_storage)):
    salt_hex = await storage.get_setting("auth_salt")
    key_hex = await storage.get_setting("auth_key")

    if not salt_hex or not key_hex:
        raise HTTPException(status_code=400, detail="No password set. Run setup first.")

    if not verify_password(body.password, salt_hex, key_hex):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_access_token("admin")
    return LoginResponse(access_token=token)


@router.post("/verify")
async def verify_token(username: str = Depends(get_current_user)):
    return {"username": username, "authenticated": True}
