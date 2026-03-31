# Production Deployment Guide

## Overview

This document outlines all tasks required to make the HostelManager API production-ready. Tasks are categorized by phase: **Development**, **Pre-Production**, and **Production**.

---

## Phase 1: Development Tasks

These tasks should be completed during development to ensure the codebase is stable.

### 1.1 Fix Critical Security Issues

- [ ] **Remove DEMO_OTP bypass**
  - Location: `app/services/auth_service.py`
  - Action: Remove or disable the `130499` demo OTP
  - Set `DEMO_OTP=""` in `.env` for all environments except local dev

- [ ] **Add security headers middleware**
  - Location: `app/main.py`
  - Install: `pip install secure`
  - Add SecurityHeadersMiddleware with:
    ```python
    from secure import SecureHeaders
    secure = SecureHeaders()
    app.middleware("http")(secure.headers)
    ```
  - Headers to include:
    - `Strict-Transport-Security`
    - `X-Content-Type-Options: nosniff`
    - `X-Frame-Options: DENY`
    - `Content-Security-Policy`
    - `Cache-Control` for sensitive endpoints

### 1.2 Fix Race Conditions

- [ ] **Add existence validations to tenant_service.py**
  ```python
  # In create_tenant() and update_tenant(), add:
  async def _validate_references():
      if tenant_data.get("propertyId"):
          prop = await db["properties"].find_one({"_id": ObjectId(tenant_data["propertyId"])})
          if not prop:
              raise ValueError("Property not found")
      
      if tenant_data.get("roomId"):
          room = await db["rooms"].find_one({"_id": ObjectId(tenant_data["roomId"])})
          if not room:
              raise ValueError("Room not found")
      
      if tenant_data.get("bedId"):
          bed = await db["beds"].find_one({"_id": ObjectId(tenant_data["bedId"])})
          if not bed:
              raise ValueError("Bed not found")
  ```

- [ ] **Add positive rent validation**
  ```python
  # In tenant_service.py create_tenant()
  rent = tenant_data.get("rent", "0")
  try:
      rent_amount = float(rent.replace(",", "").replace("₹", ""))
      if rent_amount < 0:
          raise ValueError("Rent amount cannot be negative")
  except ValueError:
      raise ValueError("Invalid rent amount")
  ```

### 1.3 Add Missing Database Indexes

Create indexes to ensure query performance:

```javascript
// Run these in MongoDB shell or via mongosh

use hostelmanager

// Token blacklist TTL index
db.token_blacklist.createIndex(
    { "expiresAt": 1 },
    { expireAfterSeconds: 0 }
)

// Login attempts TTL index
db.login_attempts.createIndex(
    { "updatedAt": 1 },
    { expireAfterSeconds: 3600 }
)

// OTP attempts TTL index  
db.otp_attempts.createIndex(
    { "updatedAt": 1 },
    { expireAfterSeconds: 3600 }
)

// Email OTPs TTL index
db.email_otps.createIndex(
    { "expires_at": 1 },
    { expireAfterSeconds: 0 }
)

// Subscriptions compound index
db.subscriptions.createIndex({ "ownerId": 1, "plan": 1 }, { unique: true })
db.subscriptions.createIndex({ "ownerId": 1, "status": 1 })

// Users email index
db.users.createIndex({ "email": 1 }, { unique: true })

// Razorpay orders index
db.razorpay_orders.createIndex({ "order_id": 1 }, { unique: true })

// Tenants indexes (if not already exists)
db.tenants.createIndex({ "propertyId": 1, "tenantStatus": 1 })
db.tenants.createIndex({ "bedId": 1 })

// Beds indexes
db.beds.createIndex({ "roomId": 1, "status": 1 })
```

### 1.4 Add Input Validation

- [ ] **Add Pydantic validation in room_service.py**
  ```python
  # In create_room() and update_room()
  if number_of_beds <= 0:
      raise ValueError("numberOfBeds must be greater than 0")
  ```

- [ ] **Add unique bed number validation per room**
  ```python
  # In create_bed()
  existing = await self.collection.find_one({
      "roomId": bed_data.get("roomId"),
      "bedNumber": bed_data.get("bedNumber"),
      "isDeleted": {"$ne": True}
  })
  if existing:
      raise ValueError("Bed number already exists in this room")
  ```

### 1.5 Improve Error Handling

- [ ] **Make get_usage() errors explicit in subscription_service.py:226-233**
  ```python
  # Replace silent zero return with logging
  logger.warning("get_usage_failed", extra={
      "event": "get_usage_failed",
      "owner_id": owner_id,
      "error": str(e)
  })
  return Usage(properties=0, tenants=0, rooms=0, staff=0)
  ```

- [ ] **Add missing indexes validation on startup**
  - Location: `app/database/init_db.py`
  - Add index creation for all collections

---

## Phase 2: Pre-Production Tasks

These tasks must be completed before deploying to production.

### 2.1 Environment Configuration

Create `.env.production` based on `.env.example`:

```env
# ===========================================
# PRODUCTION ENVIRONMENT VARIABLES
# ===========================================

# Environment
ENV=production
DEBUG=false

# Security
SECRET_KEY=generate-a-very-long-random-string-at-least-64-chars
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
ENFORCE_HTTPS=true
DEMO_OTP=

# MongoDB
MONGO_URL=mongodb://username:password@host:27017/?authSource=admin
MONGO_DB_NAME=hostelmanager

# Razorpay (REQUIRED)
RAZORPAY_KEY_ID=your_live_key_id
RAZORPAY_KEY_SECRET=your_live_key_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret_here

# Email (Production SMTP)
SMTP_HOST=smtp.production.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASSWORD=your_email_password
EMAIL_FROM=noreply@yourdomain.com

# Logging
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_FILE_PATH=/var/log/hostelmanager/api.log

# CORS
ALLOW_CREDENTIALS=true
ALLOW_LOCAL_ORIGINS=false

# Admin Access
ADMIN_ACCESS_EMAILS=admin@yourdomain.com,ops@yourdomain.com
```

### 2.2 Database Migration

- [ ] **Backup existing data**
  ```bash
  mongodump --uri="mongodb://localhost:27017/hostelmanager" --out=/backup/pre-production-$(date +%Y%m%d)
  ```

- [ ] **Run index creation script**

- [ ] **Test data integrity**
  ```javascript
  // Verify all collections
  db.properties.countDocuments()
  db.rooms.countDocuments()
  db.beds.countDocuments()
  db.tenants.countDocuments()
  db.users.countDocuments()
  ```

### 2.3 Security Hardening

- [ ] **Configure Nginx for production**
  ```nginx
  # nginx/nginx.conf additions:
  server_tokens off;
  add_header X-Frame-Options "DENY" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-XSS-Protection "1; mode=block" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  
  # HTTPS redirect
  return 301 https://$host$request_uri;
  ```

- [ ] **Set up SSL/TLS certificates**
  ```bash
  # Using Let's Encrypt
  certbot --nginx -d yourdomain.com -d www.yourdomain.com
  ```

- [ ] **Configure firewall rules**
  ```bash
  # Allow only necessary ports
  ufw allow 22    # SSH
  ufw allow 80    # HTTP
  ufw allow 443   # HTTPS
  ufw deny 27017 # MongoDB (external access)
  ```

### 2.4 Monitoring Setup

- [ ] **Configure logging**
  ```python
  # In app/main.py or logging config
  logging.config.dictConfig({
      "version": 1,
      "disable_existing_loggers": False,
      "formatters": {
          "default": {
              "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
          }
      },
      "handlers": {
          "file": {
              "class": "logging.handlers.RotatingFileHandler",
              "filename": "/var/log/hostelmanager/api.log",
              "maxBytes": 10485760,  # 10MB
              "backupCount": 5
          }
      },
      "root": {
          "level": "INFO",
          "handlers": ["file"]
      }
  })
  ```

- [ ] **Set up health check endpoint**
  ```python
  # Already exists - verify it works
  @app.get("/health")
  async def health_check():
      return {"status": "healthy"}
  ```

---

## Phase 3: Production Deployment

### 3.1 Docker Compose Configuration

Update `deploy/docker-compose.yml`:

```yaml
version: "3.9"

services:
  mongodb:
    image: mongo:7.0
    container_name: central-mongodb
    restart: unless-stopped
    command: >
      mongod
      --auth
      --bind_ip_all
      --wiredTigerCacheSizeGB 0.25
      --setParameter diagnosticDataCollectionEnabled=false
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_ROOT_USERNAME}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD}
    volumes:
      - mongo_data:/data/db
      - mongo_config:/data/configdb
    networks:
      - shared_network
    mem_limit: 384m
    cpus: "0.5"
    healthcheck:
      test: >
        mongosh --quiet
        --username ${MONGO_ROOT_USERNAME}
        --password ${MONGO_ROOT_PASSWORD}
        --authenticationDatabase admin
        --eval "db.adminCommand('ping').ok" | grep -q 1
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s

  api:
    build:
      context: ..
      dockerfile: Dockerfile
    container_name: hostelmanager-api
    restart: unless-stopped
    environment:
      - ENV=production
      - DEBUG=false
      - SECRET_KEY=${SECRET_KEY}
      - MONGO_URL=mongodb://${MONGO_ROOT_USERNAME}:${MONGO_ROOT_PASSWORD}@mongodb:27017/?authSource=admin
      - MONGO_DB_NAME=${MONGO_DB_NAME}
      - RAZORPAY_KEY_ID=${RAZORPAY_KEY_ID}
      - RAZORPAY_KEY_SECRET=${RAZORPAY_KEY_SECRET}
      - RAZORPAY_WEBHOOK_SECRET=${RAZORPAY_WEBHOOK_SECRET}
      - ENFORCE_HTTPS=true
    depends_on:
      mongodb:
        condition: service_healthy
    networks:
      - shared_network
    mem_limit: 512m
    cpus: "1.0"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  nginx:
    # ... existing config ...

volumes:
  mongo_data:
  mongo_config:

networks:
  shared_network:
    name: shared_network
```

### 3.2 Deployment Checklist

- [ ] All Phase 1 tasks completed
- [ ] All Phase 2 tasks completed
- [ ] Environment variables set correctly
- [ ] Database indexes created and verified
- [ ] SSL certificates installed and auto-renewal configured
- [ ] Backup strategy configured
- [ ] Monitoring/alerting set up
- [ ] Load testing completed
- [ ] Security audit completed

### 3.3 Post-Deployment Verification

```bash
# 1. Check all services are running
docker-compose ps

# 2. Verify API health
curl https://yourdomain.com/health

# 3. Check MongoDB connection
docker exec hostelmanager-api python -c "
from app.database.mongodb import client
client.admin.command('ping')
print('MongoDB connection OK')
"

# 4. Test authentication flow
curl -X POST https://yourdomain.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"test123"}'

# 5. Check logs for errors
docker-compose logs --tail=100 api | grep -i error

# 6. Verify Razorpay webhook endpoint
curl -I https://yourdomain.com/api/v1/razorpay/webhook
```

---

## Phase 4: Ongoing Maintenance

### 4.1 Regular Tasks

| Task | Frequency | Notes |
|------|-----------|-------|
| Log rotation | Daily | Configured in logging |
| Database backup | Daily | Via mongodb-backup container |
| Security updates | Weekly | Update base images |
| Performance review | Monthly | Check query performance |
| SSL renewal | 90 days | Let's Encrypt auto-renewal |

### 4.2 Monitoring Alerts

Set up alerts for:
- API response time > 2s
- Error rate > 1%
- MongoDB disk usage > 80%
- Memory usage > 90%
- Failed login attempts > 10/minute

### 4.3 Backup Verification

```bash
# Verify backup exists
ls -la backups/

# Test backup restoration (staging only)
mongorestore --uri="mongodb://localhost:27017/hostelmanager_test" \
  --drop /backup/backup-$(date +%Y-%m-%d).gz
```

---

## Quick Reference

### Development (.env)
```env
ENV=development
DEBUG=true
DEMO_OTP=130499
ENFORCE_HTTPS=false
MONGO_URL=mongodb://localhost:27017/hostelmanager
LOG_LEVEL=DEBUG
LOG_TO_FILE=false
```

### Production (.env)
```env
ENV=production
DEBUG=false
DEMO_OTP=
ENFORCE_HTTPS=true
MONGO_URL=mongodb://user:pass@host:27017/?authSource=admin
LOG_LEVEL=INFO
LOG_TO_FILE=true
RAZORPAY_WEBHOOK_SECRET=required_here
```

---

## Troubleshooting

### Common Issues

**MongoDB connection refused**
```bash
# Check MongoDB is running
docker-compose ps mongodb

# Check credentials
docker exec -it mongodb mongosh -u $MONGO_ROOT_USERNAME -p $MONGO_ROOT_PASSWORD
```

**API returns 500 on startup**
```bash
# Check logs
docker-compose logs api | tail -50

# Verify env vars
docker-compose exec api env | grep -E "^(MONGO|SECRET|ENV)"
```

**Payments not generating**
```bash
# Manually trigger payment generation
curl -X POST https://yourdomain.com/api/v1/payments/generate \
  -H "Authorization: Bearer $TOKEN"

# Check scheduler is running
docker-compose logs api | grep scheduler
```

---

## File Locations

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI entry point |
| `app/config/settings.py` | Configuration |
| `app/database/mongodb.py` | Database connection |
| `app/services/tenant_service.py` | Tenant CRUD |
| `app/services/payment_service.py` | Payment handling |
| `app/routes/` | API endpoints |
| `deploy/docker-compose.yml` | Production deployment |
| `.env` | Environment variables |

---

## Contact

For issues or questions, contact: ops@yourdomain.com
