"""Loads the committed Aster Electronics catalog fixture (plan section 31).

The fixture is generated ahead of time and committed under
`fixtures/catalog/aster_catalog.json` — it is never regenerated at runtime.
Parsed once per process and cached at module level; callers receive deep
copies so nothing can mutate the shared catalog by accident.
"""

from __future__ import annotations

import copy
import json
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


def load_catalog() -> list[dict]:
    """Return the full catalog as a list of MerchantOffer-shaped dicts."""
    doc = _raw_doc()
    return copy.deepcopy(doc["products"])


def catalog_merchant() -> dict:
    """Merchant identity block from the fixture header."""
    return copy.deepcopy(_raw_doc()["merchant"])
