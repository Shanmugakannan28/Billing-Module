import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Role, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_access_token(username: str, role: Role) -> str:
    # role travels inside the token (Level 11) so every request re-derives
    # permissions from the signed token instead of a second DB lookup
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "role": role.value, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username = payload.get("sub")
    except jwt.PyJWTError:
        raise unauthorized
    user = db.scalar(select(User).where(User.username == username)) if username else None
    if user is None:
        raise unauthorized
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Level 11: admin-only actions (doctor/patient delete, doctor writes)."""
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


def require_admin_or_self_doctor(doctor_id: int, user: User = Depends(get_current_user)) -> User:
    """Level 11: admin can act on any doctor; a doctor role can only act on
    the doctor record linked to their own account.

    This is used directly as `Depends(require_admin_or_self_doctor)` (not
    called first) - FastAPI matches this function's `doctor_id` parameter to
    the route's `{doctor_id}` path segment automatically."""
    if user.role == Role.admin:
        return user
    if user.role == Role.doctor and user.doctor_id == doctor_id:
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not authorized for this doctor")
