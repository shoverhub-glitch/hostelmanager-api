from dotenv import load_dotenv
from app.utils.config_helpers import require, optional

load_dotenv()

# Required
MONGO_URL = require("MONGO_URL", "Database connection required")
JWT_SECRET = require("JWT_SECRET", "JWT secret required")
FROM_EMAIL = require("FROM_EMAIL", "Email sender required")
ZEPTO_MAIL_API_KEY = require("ZEPTO_MAIL_API_KEY", "Email API key required")

# Optional with sensible defaults
ENV = optional("ENV", "production")
API_VERSION = "v1"
MONGO_DB_NAME = optional("MONGO_DB_NAME", "hostel_manager")
ALLOWED_ORIGINS = optional("ALLOWED_ORIGINS", "*")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_MINUTES = 43200
DEMO_OTP = optional("DEMO_OTP", "")
ENFORCE_HTTPS = optional("ENFORCE_HTTPS", "false").lower() == "true"
ALLOW_CREDENTIALS = optional("ALLOW_CREDENTIALS", "false").lower() == "true"
ALLOW_LOCAL_ORIGINS = optional("ALLOW_LOCAL_ORIGINS", "true").lower() == "true"
LOCAL_DEV_ORIGIN_REGEX = optional("LOCAL_DEV_ORIGIN_REGEX", r"^https?://(localhost|127\.0\.0.1)(:\d+)?$")
MONGO_RETRY_WRITES = True

_base = f"/api/{API_VERSION}"
PUBLIC_PATHS = optional("PUBLIC_PATHS", f"{_base}/health,{_base}/auth/login,{_base}/auth/register,{_base}/auth/refresh,{_base}/auth/forgot-password,{_base}/auth/verify-reset-otp,{_base}/auth/reset-password,{_base}/auth/email/send-otp,{_base}/auth/email/verify-otp,{_base}/auth/email/resend-otp")

# Quota Limits
MAX_PROPERTIES_PER_OWNER = int(optional("MAX_PROPERTIES_PER_OWNER", "3"))
MAX_TENANTS_PER_PROPERTY = int(optional("MAX_TENANTS_PER_PROPERTY", "100"))
MAX_ROOMS_PER_PROPERTY = int(optional("MAX_ROOMS_PER_PROPERTY", "40"))
MAX_STAFF_PER_PROPERTY = int(optional("MAX_STAFF_PER_PROPERTY", "7"))