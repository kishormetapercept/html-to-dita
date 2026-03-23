from datetime import datetime, timedelta, timezone
import hashlib

from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings


settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _pre_hash_password(password: str) -> str:
    """Pre-hash password with SHA256 before bcrypt to handle passwords > 72 bytes"""
    return hashlib.sha256(password.encode()).hexdigest()


def hash_password(password: str) -> str:
    # Pre-hash to ensure the input to bcrypt is always 64 chars (256 bits in hex)
    # This prevents "password cannot be longer than 72 bytes" error
    pre_hashed = _pre_hash_password(password)
    return pwd_context.hash(pre_hashed)


def verify_password(password: str, hashed_password: str) -> bool:
    # Apply the same pre-hashing before verification
    pre_hashed = _pre_hash_password(password)
    return pwd_context.verify(pre_hashed, hashed_password)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
