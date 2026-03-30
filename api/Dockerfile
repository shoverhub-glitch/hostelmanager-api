# ─── Stage 1: Builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip --root-user-action=ignore \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt --root-user-action=ignore


# ─── Stage 2: MongoDB tools installer ───────────────────────────────
FROM python:3.11-slim AS mongo-tools

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    && wget -qO - https://www.mongodb.org/static/pgp/server-8.0.asc \
    | gpg --dearmor -o /usr/share/keyrings/mongodb-archive-keyring.gpg \
    && echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-archive-keyring.gpg ] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" \
    | tee /etc/apt/sources.list.d/mongodb-org-8.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/*


# ─── Stage 3: Final runtime image ───────────────────────────────────
FROM python:3.11-slim AS final

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install curl for healthcheck (minimal, no extras)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Copy only the mongo tool binaries needed
COPY --from=mongo-tools /usr/bin/mongodump /usr/bin/mongodump
COPY --from=mongo-tools /usr/bin/mongorestore /usr/bin/mongorestore
COPY --from=mongo-tools /usr/bin/mongoexport /usr/bin/mongoexport
COPY --from=mongo-tools /usr/bin/mongoimport /usr/bin/mongoimport

# Copy shared libs required by mongo tools
COPY --from=mongo-tools /usr/lib/x86_64-linux-gnu/libssl.so* /usr/lib/x86_64-linux-gnu/
COPY --from=mongo-tools /usr/lib/x86_64-linux-gnu/libcrypto.so* /usr/lib/x86_64-linux-gnu/

# Copy application code
COPY . .

# Create non-root user
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "app.main:app"]