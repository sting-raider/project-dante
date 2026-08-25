"""Loads the committed Aster Electronics catalog fixture (plan section 31).

The fixture is generated ahead of time and committed under
`fixtures/catalog/aster_catalog.json` — it is never regenerated at runtime.
Parsed once per process and cached at module level; callers receive deep
copies so nothing can mutate the shared catalog by accident.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

# apps/api/project_dante/integrations/merchant/catalog_loader.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
CATALOG_PATH = _REPO_ROOT / "fixtures" / "catalog" / "aster_catalog.json"


@lru_cache(maxsize=1)
def _raw_doc() -> dict:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"Aster catalog fixture missing at {CATALOG_PATH}. "
            "It is a committed fixture — see fixtures/catalog/README.md."
        )
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _stamp_delivery_dates(products: list[dict]) -> None:
    """Quote a concrete promised-by date on every listing (in place).

    A live merchant API states delivery dates as of query time, not open-ended
    day ranges. Deriving promised_by_date = today + max_days here gives the
    promise pipeline a freezable, verifiable deadline (baseline-material key
    `delivery.promised_by_date`) so post-purchase SLA verification works.
    """
    today = date.today()
    for product in products:
        dp = product.get("delivery_promise") or {}
        if not dp.get("promised_by_date") and isinstance(dp.get("max_days"), int):
            dp["promised_by_date"] = (today + timedelta(days=dp["max_days"])).isoformat()


def load_catalog() -> list[dict]:
    """Return the full catalog as a list of MerchantOffer-shaped dicts."""
    doc = _raw_doc()
    products = copy.deepcopy(doc["products"])
    _stamp_delivery_dates(products)
    return products


def catalog_merchant() -> dict:
    """Merchant identity block from the fixture header."""
    return copy.deepcopy(_raw_doc()["merchant"])
