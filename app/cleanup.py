"""Render cron: python -m app.cleanup  (deletes jobs older than 24 h)."""
import os
from .db import connect

if __name__ == "__main__":
    print("deleted", connect(os.environ["DATABASE_URL"]).delete_older_than(24 * 3600), "jobs")
