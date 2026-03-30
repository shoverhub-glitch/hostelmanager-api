from slowapi import Limiter
from slowapi.util import get_remote_address

# Global limiter used by main.py via app.state.limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
)

# Decorator for normal auth actions like login
rate_limit_dep = limiter.limit("10/minute")

# Stricter decorator for sensitive endpoints (OTP, register, reset)
sensitive_action_limit = limiter.limit("5/minute")
