"""Request/response models for the authentication API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        """
        Length is the dominant factor, so the floor is 12 rather than a
        character-class rule that mostly produces `Password1!`.
        """
        if len(v.strip()) < 12:
            raise ValueError("Password must be at least 12 characters")
        if v.lower() in {"password1234", "123456789012", "qwertyuiop12"}:
            raise ValueError("Password is too common")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"email": "asha@example.in", "password": "correct-horse-battery"}
        }
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    # Optional: clients that already hold a code can complete login in one hop.
    totp_code: Optional[str] = Field(default=None, max_length=10)


class MFAVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    mfa_enabled: bool
    # True once the *current* token satisfies MFA; bank linkage requires it.
    mfa_satisfied: bool


class MFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    mfa_token: str
    detail: str = "Submit the TOTP code to POST /api/v1/auth/mfa/verify"


class MFAEnrolResponse(BaseModel):
    secret: str
    provisioning_uri: str
    detail: str = (
        "Scan the URI in an authenticator app, then confirm with " "POST /api/v1/auth/mfa/confirm"
    )


class MeResponse(BaseModel):
    user_id: str
    email: str
    mfa_enabled: bool
    mfa_satisfied: bool
    created_at: str


class SimpleResponse(BaseModel):
    success: bool = True
    detail: str
