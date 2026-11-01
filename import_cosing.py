from __future__ import annotations

import argparse

from app.database.session import get_session_factory
from app.services.cosing_cache import import_cosing_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Import an official CosIng CSV into the ingredient cache.")
    parser.add_argument("csv_path", help="Path to the CosIng CSV/export")
    args = parser.parse_args()
    session_factory = get_session_factory()
    if session_factory is None:
        raise SystemExit("DATABASE_URL is required")
    with session_factory() as session:
        count = import_cosing_csv(session, args.csv_path)
    print(f"Imported {count} CosIng ingredient rows.")


if __name__ == "__main__":
    main()
