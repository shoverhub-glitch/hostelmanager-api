#!/usr/bin/env python3
import os
import sys
import argparse
from pymongo import MongoClient


def main():
    parser = argparse.ArgumentParser(description="Drop a MongoDB database")
    parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("MONGO_DB_NAME")

    if not mongo_url or not db_name:
        print("Error: MONGO_URL and MONGO_DB_NAME environment variables are required")
        sys.exit(1)

    if not args.force:
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
