# FUTURE — everything consciously cut or deferred

Per master plan §44/§60. Nothing below is claimed by the P0 demo.

## Deferred during build (recorded at integration)

- Postgres/Redis swap-in for the JSON store (interface already mirrors relational model).
- ARQ worker queue for webhook processing (P0 processes inline, fast-ACK design kept).
- OpenTelemetry traces + Sentry.
- Merchant MCP server exposure (REST merchant interface ships first).
- Partial-refund second demo scenario (policy engine supports it; UI flow deferred if time collapses).

## Post-buildathon direction (plan §60)

- Buyer-side MCP/A2A agent; browser extension capturing purchase promises across stores.
- Merchant SDK for machine-readable promise/rights data; cross-merchant entitlement wallet.
- UCP order-lifecycle adapter; AP2 authorization adapter (mandate artifacts).
- Manufacturer warranty agent network; credit-card purchase protection connectors.
- Real logistics integration; price protection watcher; evidence-pack generator for
  consumer-helpline filings; outcome-verification plugins per product category.
