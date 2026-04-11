import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.database.mongodb import db

logger = logging.getLogger(__name__)

async def db_cleanup_job():
    """Cleanup expired OTPs and old attempt records."""
    now = datetime.now(timezone.utc)
    
    await db["email_otps"].delete_many({"expires_at": {"$lt": now}})
    
    attempt_expiry = now - timedelta(hours=24)
    await db["login_attempts"].delete_many({"updatedAt": {"$lt": attempt_expiry}})
    await db["otp_attempts"].delete_many({"updatedAt": {"$lt": attempt_expiry}})
    
    logger.info(f"✓ Database cleanup completed at {now}")

def setup_scheduler(app):
    scheduler_logger = logging.getLogger('apscheduler.executors.default')
    scheduler_logger.setLevel(logging.INFO)

    scheduler = AsyncIOScheduler()
    
    from app.services.tenant_service import TenantService
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
    
    # Job 2: Database cleanup every hour
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
    
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("✓ Background scheduler initialized")
    return scheduler