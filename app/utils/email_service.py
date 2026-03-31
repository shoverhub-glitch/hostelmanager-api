"""Email service for sending OTP and other notifications via Zoho Zepto Mail"""
import asyncio
import httpx
from typing import Optional
import logging
from app.config.settings import ZEPTO_MAIL_API_KEY, FROM_EMAIL

# Zoho Zepto Mail API endpoint - using India region endpoint
ZEPTO_API_ENDPOINT = "https://api.zeptomail.in/v1.1/email"
logger = logging.getLogger(__name__)


def _get_zepto_authorization_header(api_key: str) -> str:
    """Build Zepto auth header while tolerating env values with/without prefix."""
    raw_key = (api_key or "").strip()
    prefix = "Zoho-enczapikey"

    if raw_key.lower().startswith(prefix.lower()):
        raw_key = raw_key[len(prefix):].strip()

    return f"{prefix} {raw_key}"


async def send_otp_email(
    email: str,
    otp: str,
    app_name: str = "Hostel Manager",
    otp_type: str = "registration",
) -> bool:
    """
    Send OTP to user's email via Zoho Zepto Mail
    Args:
        email: User's email address
        otp: 6-digit OTP code
        app_name: Application name for email template
        otp_type: OTP type (registration or password_reset)
    Returns:
        True if email sent successfully, False otherwise
    """
    if not ZEPTO_MAIL_API_KEY or not FROM_EMAIL:
        logger.warning("Zepto Mail is not configured")
        return False

    try:
        # Build template by OTP use-case
        if otp_type == "password_reset":
            subject = f"Your {app_name} Password Reset Code"
            html_content = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                    <div style="background-color: #ffffff; padding: 30px; border-radius: 8px; max-width: 400px; margin: 0 auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <h2 style="color: #333; text-align: center; margin-bottom: 20px;">Reset Your Password</h2>

                        <p style="color: #666; font-size: 14px; line-height: 1.6; text-align: center;">
                            We received a request to reset your {app_name} account password. Use the code below to continue.
                        </p>

                        <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 6px; margin: 25px 0;">
                            <p style="margin: 0; font-size: 12px; color: #999; text-transform: uppercase; letter-spacing: 2px;">Your reset code</p>
                            <p style="margin: 10px 0 0 0; font-size: 32px; font-weight: bold; color: #007bff; letter-spacing: 5px;">{otp}</p>
                        </div>

                        <p style="color: #999; font-size: 13px; text-align: center; margin-bottom: 10px;">
                            This code will expire in 5 minutes
                        </p>

                        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                        <p style="color: #999; font-size: 12px; text-align: center; margin-bottom: 0;">
                            If you did not request a password reset, please ignore this email and keep your account secure.
                        </p>

                        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 6px; margin-top: 20px;">
                            <p style="margin: 0; color: #666; font-size: 12px; text-align: center;">
                                <strong>Security Note:</strong> Never share your reset code with anyone.
                                {app_name} staff will never ask for this code.
                            </p>
                        </div>
                    </div>
                </body>
            </html>
            """
        else:
            subject = f"Your {app_name} Verification Code"
            html_content = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                    <div style="background-color: #ffffff; padding: 30px; border-radius: 8px; max-width: 400px; margin: 0 auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <h2 style="color: #333; text-align: center; margin-bottom: 20px;">Verify Your Email</h2>

                        <p style="color: #666; font-size: 14px; line-height: 1.6; text-align: center;">
                            Welcome to {app_name}! Use the verification code below to complete your registration.
                        </p>

                        <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 6px; margin: 25px 0;">
                            <p style="margin: 0; font-size: 12px; color: #999; text-transform: uppercase; letter-spacing: 2px;">Your verification code</p>
                            <p style="margin: 10px 0 0 0; font-size: 32px; font-weight: bold; color: #007bff; letter-spacing: 5px;">{otp}</p>
                        </div>

                        <p style="color: #999; font-size: 13px; text-align: center; margin-bottom: 10px;">
                            This code will expire in 5 minutes
                        </p>

                        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                        <p style="color: #999; font-size: 12px; text-align: center; margin-bottom: 0;">
                            If you didn't request this code, you can safely ignore this email.
                        </p>

                        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 6px; margin-top: 20px;">
                            <p style="margin: 0; color: #666; font-size: 12px; text-align: center;">
                                <strong>Security Note:</strong> Never share your verification code with anyone.
                                {app_name} staff will never ask for your code.
                            </p>
                        </div>
                    </div>
                </body>
            </html>
            """

        # Zepto Mail API payload
        payload = {
            "from": {
                "address": FROM_EMAIL,
                "name": "Hostel Manager"
            },
            "to": [
                {
                    "email_address": {
                        "address": email
                    }
                }
            ],
            "subject": subject,
            "htmlbody": html_content
        }

        headers = {
            "Authorization": _get_zepto_authorization_header(ZEPTO_MAIL_API_KEY),
            "Content-Type": "application/json"
        }

        # Send email using httpx for async operation
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(ZEPTO_API_ENDPOINT, json=payload, headers=headers)
                if response.status_code in [200, 201, 202]:
                    logger.info("OTP email sent successfully")
                    return True
                else:
                    logger.error(
                        "Failed to send OTP email",
                        extra={
                            "status_code": response.status_code,
                            "response_text": response.text[:500],
                        },
                    )
                    return False
        except (asyncio.TimeoutError, httpx.TimeoutException):
            logger.error("Timeout while sending OTP email")
            return False
        except httpx.RequestError as request_error:
            logger.error(f"Network error while sending OTP email: {request_error}")
            return False
    except Exception as e:
        logger.exception("Unexpected error while sending OTP email")
        return False


async def send_welcome_email(email: str, name: str, app_name: str = "Hostel Manager") -> bool:
    """
    Send welcome email after successful registration
    Args:
        email: User's email address
        name: User's full name
        app_name: Application name
    Returns:
        True if email sent successfully, False otherwise
    """
    if not ZEPTO_MAIL_API_KEY or not FROM_EMAIL:
        logger.warning("Zepto Mail is not configured. Welcome email skipped")
        return False

    try:
        first_name = name.split()[0] if name and name.strip() else name or "there"
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="background-color: #ffffff; padding: 30px; border-radius: 8px; max-width: 500px; margin: 0 auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; text-align: center; margin-bottom: 20px;">Welcome to {app_name}! 🎉</h2>
                    
                    <p style="color: #666; font-size: 14px; line-height: 1.6;">
                        Hi {first_name},
                    </p>
                    
                    <p style="color: #666; font-size: 14px; line-height: 1.6;">
                        Thank you for registering with {app_name}. Your account has been successfully created and verified.
                    </p>
                    
                    <p style="color: #666; font-size: 14px; line-height: 1.6;">
                        You can now log in to your account and start managing your hostel operations efficiently.
                    </p>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="#" style="background-color: #007bff; color: white; text-decoration: none; padding: 12px 30px; border-radius: 6px; font-weight: bold; display: inline-block;">
                            Get Started
                        </a>
                    </div>
                    
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    
                    <p style="color: #999; font-size: 12px; text-align: center; margin-bottom: 0;">
                        If you have any questions, feel free to reach out to our support team.
                    </p>
                </div>
            </body>
        </html>
        """

        payload = {
            "from": {
                "address": FROM_EMAIL,
                "name": "Hostel Manager"
            },
            "to": [
                {
                    "email_address": {
                        "address": email,
                    }
                }
            ],
            "subject": f"Welcome to {app_name}!",
            "htmlbody": html_content
        }

        headers = {
            "Authorization": _get_zepto_authorization_header(ZEPTO_MAIL_API_KEY),
            "Content-Type": "application/json"
        }

        # Send email using httpx for async operation
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(ZEPTO_API_ENDPOINT, json=payload, headers=headers)
                if response.status_code in [200, 201, 202]:
                    logger.info("Welcome email sent successfully")
                    return True
                else:
                    logger.error(
                        "Failed to send welcome email",
                        extra={
                            "status_code": response.status_code,
                            "response_text": response.text[:500],
                        },
                    )
                    return False
        except (asyncio.TimeoutError, httpx.TimeoutException):
            logger.error("Timeout while sending welcome email")
            return False
        except httpx.RequestError as request_error:
            logger.error(f"Network error while sending welcome email: {request_error}")
            return False
    except Exception as e:
        logger.exception("Unexpected error while sending welcome email")
        return False


async def send_renewal_reminder_email(
    email: str,
    name: str,
    plan_name: str,
    amount_str: str,
    expiry_date: str,
    payment_link: str,
    app_name: str = "Hostel Manager"
) -> bool:
    """
    Send subscription renewal reminder with payment link
    Args:
        email: User's email address
        name: User's name
        plan_name: Subscription plan name
        amount_str: Formatted amount string (e.g., "₹999")
        expiry_date: Subscription expiry date
        payment_link: Razorpay payment link URL
        app_name: Application name
    Returns:
        True if email sent successfully, False otherwise
    """
    if not ZEPTO_MAIL_API_KEY or not FROM_EMAIL:
        logger.warning("Zepto Mail is not configured. Renewal reminder email skipped")
        return False

    try:
        first_name = name.split()[0] if name and name.strip() else name or "there"
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="background-color: #ffffff; padding: 30px; border-radius: 8px; max-width: 500px; margin: 0 auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; text-align: center; margin-bottom: 20px;">Your {app_name} Subscription is Expiring Soon</h2>
                    
                    <p style="color: #666; font-size: 14px; line-height: 1.6;">
                        Hi {first_name},
                    </p>
                    
                    <p style="color: #666; font-size: 14px; line-height: 1.6;">
                        Your {plan_name.title()} plan subscription will expire on <strong>{expiry_date}</strong>. 
                        To continue enjoying uninterrupted access to all features, please renew your subscription.
                    </p>
                    
                    <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: center;">
                        <p style="color: #666; font-size: 14px; margin: 0 0 10px 0;">Renewal Amount</p>
                        <p style="color: #007bff; font-size: 28px; font-weight: bold; margin: 0;">{amount_str}</p>
                        <p style="color: #999; font-size: 12px; margin: 10px 0 0 0;">{plan_name.title()} Plan</p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{payment_link}" style="background-color: #28a745; color: white; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
                            Pay Now
                        </a>
                    </div>
                    
                    <p style="color: #999; font-size: 12px; line-height: 1.6;">
                        Click the button above to complete your payment securely via Razorpay. 
                        Your subscription will be extended immediately after successful payment.
                    </p>
                    
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    
                    <p style="color: #999; font-size: 12px; text-align: center; margin-bottom: 0;">
                        If you did not request this renewal, please ignore this email or contact support.
                    </p>
                </div>
            </body>
        </html>
        """

        payload = {
            "from": {
                "address": FROM_EMAIL,
                "name": app_name
            },
            "to": [
                {
                    "email_address": {
                        "address": email,
                        "name": name
                    }
                }
            ],
            "subject": f"Your {app_name} Subscription Renewal - Action Required",
            "htmlbody": html_content
        }

        headers = {
            "Authorization": _get_zepto_authorization_header(ZEPTO_MAIL_API_KEY),
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(ZEPTO_API_ENDPOINT, json=payload, headers=headers)
                if response.status_code in [200, 201, 202]:
                    logger.info(f"Renewal reminder email sent to {email}")
                    return True
                else:
                    logger.error(
                        "Failed to send renewal reminder email",
                        extra={
                            "status_code": response.status_code,
                            "response_text": response.text[:500],
                        },
                    )
                    return False
        except (asyncio.TimeoutError, httpx.TimeoutException):
            logger.error("Timeout while sending renewal reminder email")
            return False
        except httpx.RequestError as request_error:
            logger.error(f"Network error while sending renewal reminder email: {request_error}")
            return False
    except Exception as e:
        logger.exception("Unexpected error while sending renewal reminder email")
        return False
