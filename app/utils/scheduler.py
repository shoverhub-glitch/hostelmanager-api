import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.database.mongodb import db

logger = logging.getLogger(__name__)

async def db_cleanup_job():
    """Cleanup expired OTPs and old attempt records."""
    now = datetime.now(timezone.utc)
    
    # 1. Cleanup expired OTPs using the expires_at field written by otp_memory_store.
    # Both registration and password-reset OTPs live in the same email_otps collection.
    await db["email_otps"].delete_many({"expires_at": {"$lt": now}})
    
    # 2. Cleanup old attempt records (older than 24 hours)
    attempt_expiry = now - timedelta(hours=24)
    await db["login_attempts"].delete_many({"updatedAt": {"$lt": attempt_expiry}})
    await db["otp_attempts"].delete_many({"updatedAt": {"$lt": attempt_expiry}})
    
    logger.info(f"✓ Database cleanup completed at {now}")

async def cleanup_expired_archives_job():
    """Cleanup permanently deleted archived resources after 30-day grace period."""
    from app.services.subscription_lifecycle import SubscriptionLifecycle
    try:
        await SubscriptionLifecycle.cleanup_expired_archives()
        logger.info(f"✓ Expired archives cleanup completed at {datetime.now(timezone.utc)}")
    except Exception as e:
        logger.error(f"Error during expired archives cleanup: {str(e)}")

def setup_scheduler(app):
    # Configure logging for APScheduler
    scheduler_logger = logging.getLogger('apscheduler.executors.default')
    scheduler_logger.setLevel(logging.INFO)

    scheduler = AsyncIOScheduler()
    
    from app.services.tenant_service import TenantService
    from app.services.razorpay_subscription_service import RazorpaySubscriptionService
    tenant_service = TenantService()
    
    # Job 1: Generate monthly payments daily at 00:05 UTC
    scheduler.add_job(
        tenant_service.generate_monthly_payments,
        trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="generate_monthly_payments",
        name="Generate monthly payments for tenants",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )
    
    # Job 2: Check and renew subscriptions daily at 01:00 UTC
    scheduler.add_job(
        RazorpaySubscriptionService.check_and_renew_subscriptions,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="auto_renewal_subscriptions",
        name="Check and renew expiring subscriptions",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )

    # Job 3: Database cleanup every hour
    scheduler.add_job(
        db_cleanup_job,
        trigger="interval",
        hours=1,
        id="db_cleanup",
        name="Cleanup expired OTPs and old attempt records",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    # Job 4: Cleanup expired archives daily at 02:00 UTC
    scheduler.add_job(
        cleanup_expired_archives_job,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="cleanup_expired_archives",
        name="Cleanup permanently deleted archived resources after 30-day grace",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )
    
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("✓ Background scheduler initialized")
    return scheduler
