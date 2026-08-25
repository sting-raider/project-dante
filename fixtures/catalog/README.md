# Catalog fixtures

`aster_catalog.json` — the Aster Electronics demo catalog (112 products).

- **Fictional data.** All brands, products, prices, and policies are invented
  for Project Dante's demo merchant. Nothing here is scraped from real stores.
- **Committed fixture, generated deterministically** (seeded generator, run
  once at build time). Never regenerate at runtime — Agent F owns generation;
  runtime code only reads via
  `project_dante/integrations/merchant/catalog_loader.py`.
- Every product row validates against the frozen `MerchantOffer` model
  (`apps/api/project_dante/domain/types.py`).
- Warranty metadata is deliberately mixed (~55% manufacturer / 15% seller /
  10% none / 20% unknown) so merchant analytics surface real blockers.
- Hero SKU `AST-HP-ANC-001` carries the exact demo-storyboard values
  (Rs 11,499 · ANC over-ear · manufacturer warranty IN · 2–4 day delivery),
  plus five feasible alternates and several near-miss decoys.
