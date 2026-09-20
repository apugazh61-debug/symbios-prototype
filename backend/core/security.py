"""
core/security.py
----------------
Cryptographic password hashing, JWT token lifecycle, and Role-Based Access Control (RBAC).
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, List
import secrets
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from core.config import settings
from core.logging import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token")


class UserRole(str, Enum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    SAFETY_AUDITOR = "safety_auditor"
    WORKER_HMI = "worker_hmi"


class TokenPayload(BaseModel):
    sub: str  # User ID
    email: str
    role: UserRole
    site_id: Optional[str] = None
    exp: int


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Safely verify plain password against bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


def get_password_hash(password: str) -> str:
    """Generate salted bcrypt password hash."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def create_access_token(
    subject: str,
    email: str,
    role: UserRole,
    site_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create signed HMAC-SHA256 JWT access token."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "sub": subject,
        "email": email,
        "role": role.value,
        "site_id": site_id,
        "exp": int(expire.timestamp())
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> TokenPayload:
    """Decode and cryptographically validate JWT access token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired. Please re-authenticate.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidTokenError, Exception):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user_token(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    """Dependency that returns validated current user token claims. Strictly requires valid JWT."""
    return decode_access_token(token)


class RoleChecker:
    """Dependency for strict RBAC validation."""
    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    def __call__(self, token: TokenPayload = Depends(get_current_user_token)) -> TokenPayload:
        if token.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required role: {[r.value for r in self.allowed_roles]}"
            )
        return token


# In-memory ephemeral bootstrap passwords for development mode
_ephemeral_admin_password: Optional[str] = None
_ephemeral_supervisor_password: Optional[str] = None


def get_bootstrap_admin_password() -> str:
    """
    Returns the bootstrap admin password.
    - If BOOTSTRAP_ADMIN_PASSWORD env var is set, uses it.
    - If ENVIRONMENT == 'production' and not set: raises fast startup error.
    - If ENVIRONMENT == 'development' and not set: generates random high-entropy
      password at startup, logs once to console, and holds in memory.
    """
    global _ephemeral_admin_password
    if settings.BOOTSTRAP_ADMIN_PASSWORD:
        return settings.BOOTSTRAP_ADMIN_PASSWORD

    if settings.ENVIRONMENT.lower() == "production":
        raise RuntimeError(
            "FATAL: BOOTSTRAP_ADMIN_PASSWORD environment variable is required in production! "
            "Server refusing to start with insecure default credentials."
        )

    if _ephemeral_admin_password is None:
        _ephemeral_admin_password = secrets.token_urlsafe(16)
        logger.warning(
            f"[BOOTSTRAP SECURITY] Generated ephemeral admin password for dev: "
            f"{_ephemeral_admin_password} (User: admin@symbios.ai)"
        )
        print(
            f"\n>>> [SYMBIOS SECURITY] Ephemeral Dev Admin Password: "
            f"{_ephemeral_admin_password} (admin@symbios.ai)\n"
        )
    return _ephemeral_admin_password


def get_bootstrap_supervisor_password() -> str:
    """
    Returns the bootstrap supervisor password.
    - If BOOTSTRAP_SUPERVISOR_PASSWORD env var is set, uses it.
    - If ENVIRONMENT == 'production' and not set: raises fast startup error.
    - If ENVIRONMENT == 'development' and not set: generates random high-entropy
      password at startup, logs once to console, and holds in memory.
    """
    global _ephemeral_supervisor_password
    if settings.BOOTSTRAP_SUPERVISOR_PASSWORD:
        return settings.BOOTSTRAP_SUPERVISOR_PASSWORD

    if settings.ENVIRONMENT.lower() == "production":
        raise RuntimeError(
            "FATAL: BOOTSTRAP_SUPERVISOR_PASSWORD environment variable is required in production! "
            "Server refusing to start with insecure default credentials."
        )

    if _ephemeral_supervisor_password is None:
        _ephemeral_supervisor_password = secrets.token_urlsafe(16)
        logger.warning(
            f"[BOOTSTRAP SECURITY] Generated ephemeral supervisor password for dev: "
            f"{_ephemeral_supervisor_password} (User: supervisor@symbios.ai)"
        )
        print(
            f"\n>>> [SYMBIOS SECURITY] Ephemeral Dev Supervisor Password: "
            f"{_ephemeral_supervisor_password} (supervisor@symbios.ai)\n"
        )
    return _ephemeral_supervisor_password

