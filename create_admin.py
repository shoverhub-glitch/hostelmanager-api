from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from passlib.context import CryptContext


API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

load_dotenv(API_DIR / ".env")

from app.database.mongodb import client, db  # noqa: E402
from app.services.subscription_service import SubscriptionService  # noqa: E402


PASSWORD_MIN_LENGTH = 8
INDIAN_PHONE_PATTERN = re.compile(r"^\+91[6-9]\d{9}$")
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def parse_args() -> argparse.Namespace:
    default_name = os.environ.get("ADMIN_BOOTSTRAP_NAME", "").strip()
    default_email = os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "").strip()
    default_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    default_phone = os.environ.get("ADMIN_BOOTSTRAP_PHONE", "").strip()
    default_role = os.environ.get("ADMIN_BOOTSTRAP_ROLE", "propertyowner").strip() or "propertyowner"
    default_grant_by = os.environ.get("ADMIN_BOOTSTRAP_GRANT_BY", "email").strip() or "email"

    parser = argparse.ArgumentParser(
        description="Create or grant access to a HostelManager admin user.",
    )
    parser.add_argument("--name", default=default_name or None, help="Full name for the account")
    parser.add_argument("--email", default=default_email or None, help="Email address for the account")
    parser.add_argument("--password", default=default_password or None, help="Password for a new account or password reset")
    parser.add_argument("--phone", default=default_phone or None, help="Optional phone number in +91XXXXXXXXXX format")
    parser.add_argument(
        "--role",
        default=default_role,
        help="Stored user role. This does not grant admin access unless --grant-by role is used.",
    )
    parser.add_argument(
        "--grant-by",
        choices=["email", "user-id", "role", "none"],
        default=default_grant_by,
        help="How to grant admin access in settings. Default is email for safer access control.",
    )
    parser.add_argument(
        "--env-file",
        default=str(API_DIR / ".env"),
        help="Path to the API .env file that stores admin access settings",
    )
    parser.add_argument(
        "--skip-env-update",
        action="store_true",
        help="Create or update the user without modifying ADMIN_ACCESS_* settings",
    )
    parser.add_argument(
        "--no-default-subscriptions",
        action="store_true",
        help="Do not create default subscription records for a newly created account",
    )
    args = parser.parse_args()

    missing_fields: list[str] = []
    if not args.name:
        missing_fields.append("--name or ADMIN_BOOTSTRAP_NAME")
    if not args.email:
        missing_fields.append("--email or ADMIN_BOOTSTRAP_EMAIL")

    if missing_fields:
        parser.error(f"the following values are required: {', '.join(missing_fields)}")

    return args


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_phone(phone: str | None) -> None:
    if phone and not INDIAN_PHONE_PATTERN.match(phone.strip()):
        raise ValueError("Phone must be in +91XXXXXXXXXX format")


def validate_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number")
    if not re.search(r"[^\w\s]", password):
        raise ValueError("Password must contain at least one special character")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def parse_csv(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def update_env_selector(env_path: Path, key: str, value: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$")
    updated_lines: list[str] = []
    replaced = False

    for line in lines:
        match = pattern.match(line)
        if not match:
            updated_lines.append(line)
            continue

        existing = parse_csv(match.group(1))
        merged = dedupe_preserve_order([*existing, value])
        updated_lines.append(f"{key}={','.join(merged)}")
        replaced = True

    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


async def create_or_update_admin(args: argparse.Namespace) -> None:
    normalized_email = normalize_email(args.email)
    validate_phone(args.phone)

    users_collection = db["users"]
    existing_user = await users_collection.find_one({"email": normalized_email})
    created_new_user = existing_user is None
    password = args.password

    if created_new_user and not password:
        password = getpass.getpass("Password for new admin user: ")
    if password:
        validate_password_strength(password)

    now = datetime.now(timezone.utc)

    if created_new_user:
        if not password:
            raise ValueError("Password is required when creating a new user")

        user_doc = {
            "name": args.name.strip(),
            "email": normalized_email,
            "phone": args.phone.strip() if args.phone else None,
            "password": hash_password(password),
            "role": args.role.strip(),
            "isEmailVerified": True,
            "isDeleted": False,
            "isDisabled": False,
            "lastLogin": None,
            "createdAt": now,
            "updatedAt": now,
            "propertyIds": [],
        }
        result = await users_collection.insert_one(user_doc)
        user_id = str(result.inserted_id)
        print(f"Created user: {normalized_email}")
        print(f"User ID: {user_id}")

        if not args.no_default_subscriptions:
            await SubscriptionService.create_default_subscriptions(user_id)
            print("Created default subscriptions for the new user")
    else:
        updates: dict[str, object] = {
            "name": args.name.strip(),
            "role": args.role.strip(),
            "updatedAt": now,
        }
        if args.phone:
            updates["phone"] = args.phone.strip()
        if password:
            updates["password"] = hash_password(password)
        await users_collection.update_one({"_id": existing_user["_id"]}, {"$set": updates})
        user_id = str(existing_user["_id"])
        print(f"Updated existing user: {normalized_email}")
        print(f"User ID: {user_id}")
        if password:
            print("Password was reset for the existing user")

    if args.skip_env_update or args.grant_by == "none":
        print("Skipped ADMIN_ACCESS_* settings update")
        return

    env_path = Path(args.env_file).resolve()
    if args.grant_by == "email":
        update_env_selector(env_path, "ADMIN_ACCESS_EMAILS", normalized_email)
        print(f"Granted admin access by email in {env_path}")
    elif args.grant_by == "user-id":
        update_env_selector(env_path, "ADMIN_ACCESS_USER_IDS", user_id)
        print(f"Granted admin access by user ID in {env_path}")
    elif args.grant_by == "role":
        update_env_selector(env_path, "ADMIN_ACCESS_ROLES", args.role.strip())
        print(f"Granted admin access by role in {env_path}")

    print("Restart the API server after changing .env so the new admin settings are loaded")


async def main() -> int:
    args = parse_args()
    try:
        await create_or_update_admin(args)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))