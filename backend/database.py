"""Database engine and session setup.

This project runs on PostgreSQL only. Set the DATABASE_URL environment variable
to a Postgres connection string using the psycopg (v3) dialect, e.g.:

    postgresql+psycopg://user:password@host:5432/star_crm

For Azure Database for PostgreSQL (Flexible Server), SSL is required, so append
?sslmode=require:

    postgresql+psycopg://star:PASSWORD@myserver.postgres.database.azure.com:5432/star_crm?sslmode=require

There is no SQLite fallback. The app will refuse to start without a Postgres
DATABASE_URL, so dev and production behave identically.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Load backend/.env so DATABASE_URL (and FRONTEND_ORIGINS) are picked up without
# having to export them in the shell. Real environment variables still win.
load_dotenv(Path(__file__).resolve().parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. This app requires PostgreSQL. Set it to a "
        "psycopg connection string, e.g. "
        "postgresql+psycopg://user:password@host:5432/star_crm "
        "(copy backend/.env.example to backend/.env and fill it in)."
    )

if not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError(
        f"DATABASE_URL must be a PostgreSQL URL (got '{DATABASE_URL.split('://')[0]}://...'). "
        "Use the psycopg dialect: postgresql+psycopg://user:password@host:5432/star_crm"
    )

# pool_pre_ping recycles connections that a managed Postgres (e.g. Azure) may
# have dropped while idle, avoiding stale-connection errors on the next request.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
