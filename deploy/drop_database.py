#!/usr/bin/env python3
import os
import sys
from pymongo import MongoClient


def main():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")

    if not mongo_url or not db_name:
        print("Error: MONGO_URL and DB_NAME environment variables are required")
        sys.exit(1)

    print("=" * 50)
    print("   WARNING: This will DROP the database!")
    print(f"   Database: {db_name}")
    print("=" * 50)
    confirm = input("\nType 'yes' to continue: ")
    if confirm.lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    print(f"\nConnecting to MongoDB: {mongo_url}")
    print(f"Dropping database: {db_name}")

    client = MongoClient(mongo_url)
    client.drop_database(db_name)

    print(f"Database '{db_name}' dropped successfully.")
    client.close()


if __name__ == "__main__":
    main()
