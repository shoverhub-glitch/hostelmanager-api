from dotenv import load_dotenv
from app.utils.config_helpers import require, optional

# Load environment variables from .env file
load_dotenv()

ENV = optional("ENV", "production")
API_VERSION = "v1"
APP_URL = optional("APP_URL", "https://your-app-url.com")

MONGO_URL = require(
    "MONGO_URL",
    "Required for database connectivity."
)
MONGO_DB_NAME = optional("MONGO_DB_NAME", "hostel_manager")
MONGO_RETRY_WRITES = True

JWT_SECRET = require(
    "JWT_SECRET",
    "Required for secure token generation."
)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_MINUTES = 43200

FROM_EMAIL = require(
    "FROM_EMAIL",
    "Required as the sender address for all notifications."
)
ZEPTO_MAIL_API_KEY = require(
    "ZEPTO_MAIL_API_KEY",
    "Required for sending emails via Zoho Zepto Mail."
)

RAZORPAY_KEY_ID = require(
    "RAZORPAY_KEY_ID",
    "Required for payment processing."
)
RAZORPAY_KEY_SECRET = require(
    "RAZORPAY_KEY_SECRET",
    "Required for payment processing."
)
RAZORPAY_WEBHOOK_SECRET = optional("RAZORPAY_WEBHOOK_SECRET")

ALLOWED_ORIGINS = optional("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOW_CREDENTIALS = False
ENFORCE_HTTPS = False
ALLOW_LOCAL_ORIGINS = True
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

ADMIN_ACCESS_EMAILS = optional("ADMIN_ACCESS_EMAILS")
ADMIN_ACCESS_USER_IDS = optional("ADMIN_ACCESS_USER_IDS")
ADMIN_ACCESS_ROLES = optional("ADMIN_ACCESS_ROLES", "admin")
ADMIN_ACCESS_FAIL_CLOSED = True

DEMO_OTP = optional("DEMO_OTP", "")

LOG_DIR = "logs"
LOG_FILE_BACKUP_COUNT = 30

APP_VERSION = "1.0.0"

_raw_public_paths = optional("PUBLIC_PATHS")
if _raw_public_paths:
    PUBLIC_PATHS = _raw_public_paths
else:
    _base = f"/api/{API_VERSION}"
    PUBLIC_PATHS = ",".join(
        [
            f"{_base}/health",
            f"{_base}/auth/login",
            f"{_base}/auth/register",
            f"{_base}/auth/refresh",
            f"{_base}/auth/forgot-password",
            f"{_base}/auth/verify-reset-otp",
            f"{_base}/auth/reset-password",
            f"{_base}/auth/email/send-otp",
            f"{_base}/auth/email/verify-otp",
            f"{_base}/auth/email/resend-otp",
            f"{_base}/subscription/webhook",
        ]
    )