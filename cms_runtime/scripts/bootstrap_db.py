#!/usr/bin/env python3
"""
Bootstrap a fresh CMS runtime SQLite database.

Usage:
    python scripts/bootstrap_db.py [--db PATH] [--force]

By default, creates ./data/sqlite/cms_runtime.db with the full schema
(observations + episodes) applied. Refuses to overwrite an existing
database unless --force.

Schema is applied idempotently — re-running on an existing database
is safe and will upgrade the schema in place if a newer version exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src/ is on path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


DEFAULT_DB_PATH = Path("data/sqlite/cms_runtime.db")


def bootstrap(db_path: Path, force: bool = False) -> int:
    if db_path.exists() and not force:
        print(f"  ✗ Database already exists: {db_path}")
        print(f"    Use --force to overwrite, or it will be upgraded in place.")
        # Allow in-place upgrade
        with SQLiteBackend(db_path) as backend:
            backend.bootstrap_schema(FULL_SCHEMA_DDL)
            versions = backend.fetch_all(
                "SELECT version FROM schema_version ORDER BY version"
            )
            print(f"  ✓ Schema versions present: {[v[0] for v in versions]}")
            tables = backend.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            print(f"  ✓ Tables present: {', '.join(t[0] for t in tables)}")
        return 0

    if db_path.exists() and force:
        db_path.unlink()
        print(f"  ⚠ Removed existing database: {db_path}")

    print(f"  → Creating database: {db_path}")
    with SQLiteBackend(db_path) as backend:
        backend.bootstrap_schema(FULL_SCHEMA_DDL)
        versions = backend.fetch_all(
            "SELECT version FROM schema_version ORDER BY version"
        )
        print(f"  ✓ Schema versions applied: {[v[0] for v in versions]}")
        tables = backend.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        print(f"  ✓ Tables present: {', '.join(t[0] for t in tables)}")

    print(f"\n  Done. Database ready at: {db_path.resolve()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap CMS runtime database")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"Database path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing database")
    args = parser.parse_args()
    return bootstrap(args.db, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
