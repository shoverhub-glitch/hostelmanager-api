from passlib.context import CryptContext
from passlib.hash import argon2

from jose import jwt
from datetime import datetime, timedelta, timezone
import hmac
import logging
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.config import settings
from app.database.token_blacklist import is_token_blacklisted

SECRET_KEY = settings.JWT_SECRET
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_MINUTES = settings.REFRESH_TOKEN_EXPIRE_MINUTES

if not SECRET_KEY or len(SECRET_KEY) < 32:
	raise RuntimeError("JWT_SECRET must be set and at least 32 characters long for security.")

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
logger = logging.getLogger(__name__)



# OAuth2 scheme for JWT Bearer token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
	try:
		if await is_token_blacklisted(token):
			raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

		payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
		if payload.get("type") != "access":
			raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

		user_id = payload.get("sub")
		if user_id is None:
			raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
		return user_id
	except HTTPException:
		raise
	except Exception:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")


def get_current_user_from_request(request: Request) -> dict:
	"""Read current user hydrated by UserContextMiddleware."""
	current_user = getattr(request.state, "current_user", None)
	if not current_user:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
	return current_user


def _csv_to_set(raw_value: str, to_lower: bool = False) -> set:
	if not raw_value:
		return set()
	values = {item.strip() for item in raw_value.split(",") if item and item.strip()}
	if to_lower:
		return {item.lower() for item in values}
	return values


def has_admin_access(user: dict) -> bool:
	"""Check if user has admin access via email, user ID, or role."""
	admin_emails = _csv_to_set(getattr(settings, "ADMIN_ACCESS_EMAILS", ""), to_lower=True)
	admin_user_ids = _csv_to_set(getattr(settings, "ADMIN_ACCESS_USER_IDS", ""), to_lower=False)
	admin_roles = _csv_to_set(getattr(settings, "ADMIN_ACCESS_ROLES", ""), to_lower=True)
	fail_closed = bool(getattr(settings, "ADMIN_ACCESS_FAIL_CLOSED", True))

	user_email = str(user.get("email") or "").strip().lower()
	user_id = str(user.get("id") or user.get("_id") or "").strip()
	user_role = str(user.get("role") or "").strip().lower()

	if admin_emails and user_email in admin_emails:
		return True

	if admin_user_ids and user_id in admin_user_ids:
		return True

	if admin_roles and user_role in admin_roles:
		return True

	if admin_emails or admin_user_ids or admin_roles:
		return False

	return not fail_closed





def require_admin_user(request: Request) -> dict:
	"""Allow only configured admin users to access protected endpoints."""
	current_user = get_current_user_from_request(request)
	if not has_admin_access(current_user):
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
	return current_user

def hash_password(password: str) -> str:
	# Argon2 does not have the 72-byte limit
	return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
	return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: timedelta = None):
	to_encode = data.copy()
	expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
	to_encode.update({"exp": expire, "type": "access"})
	return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, expires_delta: timedelta = None):
	to_encode = data.copy()
	expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES))
	# Add a unique jti (JWT ID) for refresh token rotation
	import uuid
	to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})
	return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
