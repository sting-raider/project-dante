# FUTURE — everything consciously cut or deferred

Per master plan §44/§60. Nothing below is claimed by the P0 demo.

## Deferred during build (recorded at integration)

- Redis/ARQ infrastructure remains deferred. Managed PostgreSQL is now the
  required final Railway store; the JSON snapshot is retained only as an
  emergency single-replica recovery fallback.
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

## Forbidden until done (finish plan §46 — scope-creep quarantine)

Nothing below may be started, claimed, or demoed until its gate is met. This list
exists so scope creep has a home: if it isn't shipped, it belongs here, not in the
README or the pitch.

| Distraction | Forbidden until… |
|---|---|
| Claiming real-gateway proof (order/pay/refund ids) | `scripts/verify_real_integration.py` passes and appends BEGIN-RUN evidence to `REAL_INTEGRATION_STATUS.md` — never hand-edit those rows to PROVEN |
| Any live-mode (`rzp_live_*`) operation | Deliberate product decision + key custody story; startup hard-reject stays until then |
| ARQ worker queue for webhooks | Inline fast-ACK path demonstrably drops a webhook under load testing |
| Multi-merchant support | The single fictional Aster Electronics arc runs clean end-to-end in a recorded video |
| Real logistics / courier integrations | Synthetic fulfillment remains labeled everywhere it renders; removing labels is forbidden outright |
| Statutory/legal-right reasoning | Legal review of claims language — until then entitlements stay "demo-defined purchase rights" |
| AP2 / UCP protocol claims | An actual protocol adapter ships behind a feature flag; positioning text stays "prior art / differentiation" only |
| LLM-driven money decisions | Never. This is an inviolable boundary (agents propose, deterministic code disposes), not a roadmap item — listed here so nobody "fixes" it |
| Partial-refund second scenario in the demo | Policy-engine support exists, but the second demo flow lands only after the primary hero video is recorded and submitted |
| Browser extension / cross-store capture | Post-buildathon per §60; not buildathon scope under any deadline pressure |
| New UI polish beyond the editorial system | All 11 SCREENSHOTS.md shots captured with badges visible; design churn after that risks the recording |
