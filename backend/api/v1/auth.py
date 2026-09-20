"""
api/v1/auth.py
--------------
Authentication, JWT token issuance, and user profile management.
"""

from collections import defaultdict
from datetime import timedelta
import time
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user_token,
    RoleChecker,
    TokenPayload,
    UserRole
)
from core.config import settings
from models.user import User
from models.audit import AuditLog
from schemas.safety import Token, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication & RBAC"])

# In-memory sliding window rate limiting for failed authentication attempts
_FAILED_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW_SECONDS = 15 * 60  # 15 minutes
_RATE_LIMIT_MAX_FAILURES = 5


def check_login_rate_limit(client_ip: str, username: str) -> None:
    """Checks if either the client IP or account has exceeded 5 failed attempts in 15 minutes."""
    now = time.time()
    for key in [f"ip:{client_ip}", f"user:{username}"]:
        timestamps = _FAILED_LOGIN_ATTEMPTS[key]
        # Keep only timestamps within the sliding window
        valid_ts = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW_SECONDS]
        _FAILED_LOGIN_ATTEMPTS[key] = valid_ts
        if len(valid_ts) >= _RATE_LIMIT_MAX_FAILURES:
            oldest = valid_ts[0]
            retry_after = max(1, int(_RATE_LIMIT_WINDOW_SECONDS - (now - oldest)))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Account/IP temporarily locked. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )


def record_login_failure(client_ip: str, username: str) -> None:
    """Records a failed login attempt against both IP and username."""
    now = time.time()
    _FAILED_LOGIN_ATTEMPTS[f"ip:{client_ip}"].append(now)
    _FAILED_LOGIN_ATTEMPTS[f"user:{username}"].append(now)


def reset_login_failures(client_ip: str, username: str) -> None:
    """Clears failure count for both account and originating IP upon successful login.
    
    Clearing the IP key prevents a shared factory VPN/NAT address from remaining
    locked out after a legitimate user authenticates successfully from that address.
    """
    _FAILED_LOGIN_ATTEMPTS.pop(f"user:{username}", None)
    _FAILED_LOGIN_ATTEMPTS.pop(f"ip:{client_ip}", None)


def _clear_rate_limits_for_test() -> None:
    """Helper to reset rate limit state in unit test suites."""
    _FAILED_LOGIN_ATTEMPTS.clear()


@router.post("/token", response_model=Token)
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "127.0.0.1")
    normalized_user = form_data.username.strip().lower()

    # Rate limiting: max 5 failed attempts per account/IP per 15 min
    check_login_rate_limit(client_ip, normalized_user)

    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        record_login_failure(client_ip, normalized_user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user account")

    # Reset failure tracking for this user upon success
    reset_login_failures(client_ip, normalized_user)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(
        subject=user.id,
        email=user.email,
        role=UserRole(user.role),
        site_id=user.site_id,
        expires_delta=access_token_expires
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": UserRole(user.role),
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES
    }


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    user_in: UserCreate,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN])),
    db: Session = Depends(get_db)
):
    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role=user_in.role.value,
        site_id=user_in.site_id
    )
    db.add(new_user)
    db.flush()

    audit = AuditLog(
        user_id=token.sub,
        user_email=token.email,
        action="CREATE_USER_ACCOUNT",
        entity_type="User",
        entity_id=new_user.id,
        severity="info",
        message=f"Admin '{token.email}' provisioned new user account '{new_user.email}' with role '{new_user.role}'.",
        changes_json=f'{{"role": "{new_user.role}", "email": "{new_user.email}"}}'
    )
    db.add(audit)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/me", response_model=UserResponse)
def get_current_user_profile(
    token_payload: TokenPayload = Depends(get_current_user_token),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == token_payload.sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
