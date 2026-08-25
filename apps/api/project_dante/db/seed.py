"""Seed the Aster Electronics catalog into the store.

Usage (from apps/api):
    .venv/Scripts/python.exe -m project_dante.db.seed
"""

from __future__ import annotations

from project_dante.integrations.merchant.service import seed_catalog


def main() -> None:
    count = seed_catalog()
    print(f"Seeded {count} Aster Electronics offers into STORE.")


if __name__ == "__main__":
    main()
