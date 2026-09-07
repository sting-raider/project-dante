"use client";

/**
 * /contract/[id]/rights — the Rights Graph (plan §28). SVG graph on top;
 * clicking an entitlement node opens a side drawer with issuer, status,
 * expiry, evidence requirements, remedy value, resolution estimate and
 * fallback relationships. Edge-type legend below.
 *
 * Agent I.
 */

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import Badge from "@/components/commerce/Badge";
import MoneyText from "@/components/commerce/MoneyText";
import RightsGraph, {
  type GraphNode,
} from "@/components/rights-graph/RightsGraph";
import { apiGet } from "@/lib/api";
import type { ContractResponse, Entitlement, RightsResponse } from "@/lib/rights-ui";
import { normalizeEdges, isTerminal } from "@/lib/rights-ui";
import { formatDateTime, prettyJson } from "@/lib/format";
import { cn } from "@/lib/cn";

const EDGE_LEGEND: { type: string; note: string }[] = [
  { type: "SUPPORTED_BY", note: "evidence or promise underwrites the purchase" },
  { type: "MATERIAL_TO", note: "promise was a reason for choosing this offer" },
  { type: "ACTIVATED_BY", note: "breach brought this right to life" },
  { type: "REQUIRES", note: "prerequisite entitlement or promise" },
  { type: "BLOCKS", note: "excludes the target right while held" },
  { type: "FALLBACK_TO", note: "next-best right if this one fails" },
  { type: "REMEDIES", note: "entitlement funds this remedy" },
  { type: "ISSUED_BY", note: "right issued against the contract" },
];

export default function RightsPage() {
  const params = useParams<{ id: string }>();
  const contractId = params.id;

  const [graph, setGraph] = useState<RightsResponse["graph"] | null>(null);
  const [entitlements, setEntitlements] = useState<Entitlement[]>([]);
  const [contractStatus, setContractStatus] = useState<string | null>(null);
  const [sandbox, setSandbox] = useState<boolean | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Drawer focus management (#15).
  const drawerRef = useRef<HTMLElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const lastTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let alive = true;
    apiGet<RightsResponse>(`/api/contracts/${contractId}/rights`)
      .then((d) => {
        if (!alive) return;
        setGraph(d.graph);
        setEntitlements(d.entitlements ?? []);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "failed to load rights"));
    apiGet<ContractResponse>(`/api/contracts/${contractId}`)
      .then((d) => {
        if (!alive) return;
        setContractStatus(d.contract.status);
        setSandbox(!!d.contract.sandbox_mode);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [contractId]);

  const fetchRights = useCallback(
    () =>
      apiGet<RightsResponse>(`/api/contracts/${contractId}/rights`)
        .then((d) => {
          setGraph(d.graph);
          setEntitlements(d.entitlements ?? []);
        })
        .catch(() => undefined),
    [contractId],
  );

  // Poll lightly so eligibility changes (replacement-unavailable etc.)
  // recolor the graph without a manual refresh — but only while the contract
  // is still moving (not terminal) and the tab is visible (#15).
  useEffect(() => {
    if (isTerminal(contractStatus) || typeof document === "undefined") return;
    if (document.hidden) return; // resumed by the visibility listener below

    const t = setInterval(fetchRights, 5000);
    return () => clearInterval(t);
  }, [contractStatus, fetchRights]);

  // When the tab is hidden mid-polling we drop the interval; when it becomes
  // visible again and polling is still warranted, restart it.
  useEffect(() => {
    function onVisibility() {
      if (!document.hidden && !isTerminal(contractStatus)) {
        void fetchRights();
      }
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [contractStatus, fetchRights]);

  // Focus the drawer on open; Escape closes; focus returns to the trigger.
  useEffect(() => {
    if (!selected) return;
    lastTriggerRef.current = document.activeElement as HTMLElement | null;
    const raf = requestAnimationFrame(() => closeBtnRef.current?.focus());
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setSelected(null);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown);
      lastTriggerRef.current?.focus?.();
    };
  }, [selected]);

  const nodes = graph?.nodes ?? [];
  const edges = useMemo(
    () => (graph ? normalizeEdges(graph.edges) : []),
    [graph]
  );

  /** Entitlement record behind the selected node, for the drawer card. */
  const selectedEntitlement: Entitlement | undefined = useMemo(() => {
    if (!selected) return undefined;
    const rawId = selected.id.replace(/^(ent|remedy|promise|evidence|purchase):/, "");
    return entitlements.find((e) => e.id === rawId || e.id === selected.id || e.slug === rawId);
  }, [selected, entitlements]);

  const entitlementCount = entitlements.length;
  const eligibleCount = entitlements.filter(
    (entitlement) => entitlement.status === "eligible" || entitlement.status === "active",
  ).length;
  const lineScopedCount = entitlements.filter((entitlement) => entitlement.line_item_id).length;
  const breachNodeCount = nodes.filter((node) => node.type === "breach").length;

  function entitlementTitle(e: Entitlement): string {
    return e.slug ?? `${e.type} · ${e.issuer_name}`;
  }

  return (
    <main className="rights-dossier-page dante-container py-8 md:py-12">
      <Folio
        issue="ISSUE 05 / RIGHTS"
        running={`DOSSIER / ${contractId.slice(0, 13).toUpperCase()}`}
        href={`/contract/${contractId}`}
      />

      <header className="rights-masthead mt-8 grid grid-cols-1 gap-6 md:grid-cols-12">
        <div className="md:col-span-8">
          <SectionLabel>THE PURCHASE RIGHTS GRAPH</SectionLabel>
          <h1 className="mt-3 font-display text-5xl leading-[1.02] md:text-6xl">
            What this purchase entitles you to.
          </h1>
          <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-soft">
            Every right is derived — not remembered by a support agent. Nodes are
            promises, breaches, entitlements, evidence and remedies; edges are
            the legal-ish relationships between them. Select an entitlement node
            for its full terms.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 md:col-span-4 md:items-end">
          {contractStatus ? <Badge>{contractStatus}</Badge> : null}
          <span className="folio-label">
            {sandbox === null ? "PAYMENT RAIL · PROBING…" : sandbox ? "SANDBOX RAIL" : "LIVE TEST-MODE RAIL"}
          </span>
          {(entitlements.filter((e) => e.status === "eligible").length > 0 && (
            <span className="folio-label text-success">
              {entitlements.filter((e) => e.status === "eligible").length} ELIGIBLE
            </span>
          )) ||
            null}
          <Link
            href={`/contract/${contractId}/remedy`}
            className="folio-label underline-offset-4 hover:text-signal hover:underline"
          >
            Remedy planner →
          </Link>
        </div>
      </header>

      <div className="rights-summary-grid" aria-label="Rights graph summary">
        <div className="rights-summary-card">
          <span className="rights-summary-label">Graph state</span>
          <strong className="rights-summary-value">{nodes.length || "—"}</strong>
          <span className="rights-summary-detail">nodes · {edges.length || "—"} relationships</span>
        </div>
        <div className="rights-summary-card">
          <span className="rights-summary-label">Purchase rights</span>
          <strong className="rights-summary-value">{entitlementCount || "—"}</strong>
          <span className="rights-summary-detail">{eligibleCount} eligible or active</span>
        </div>
        <div className="rights-summary-card">
          <span className="rights-summary-label">Line scope</span>
          <strong className="rights-summary-value">{lineScopedCount || "—"}</strong>
          <span className="rights-summary-detail">entitlements bound to a basket line</span>
        </div>
        <div className="rights-summary-card">
          <span className="rights-summary-label">Verification input</span>
          <strong className="rights-summary-value">{breachNodeCount || "Watching"}</strong>
          <span className="rights-summary-detail">{breachNodeCount === 1 ? "breach node" : "breach nodes"} activating rights</span>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-8 border-l-2 border-danger pl-3 font-mono text-xs text-danger">
          {error} — is the API on :8000?
        </p>
      )}

      {!graph && !error && (
        <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
          Deriving rights…
        </p>
      )}

      {graph && (
        <div
          className={cn(
            "rights-graph-surface relative mt-10 overflow-x-auto rounded-md border border-rule bg-paper-bright p-4",
            selected && "lg:pr-[26rem]"
          )}
        >
          <RightsGraph
            nodes={nodes}
            edges={edges}
            onSelect={(n) =>
              setSelected((cur) => (cur?.id === n.id ? null : n))
            }
            selectedId={selected?.id ?? null}
          />

          {/* Drawer — focus lands on close on open; Escape closes; focus
              returns to the triggering node (#15). */}
          {selected && (
            <aside
              ref={drawerRef}
              role="dialog"
              aria-label={`Entitlement detail: ${selected.label}`}
              className={cn(
                "rights-drawer mt-6 rounded-md border border-ink bg-paper p-5 lg:absolute lg:right-4 lg:top-4 lg:mt-0 lg:max-h-[calc(100%-2rem)] lg:w-96 lg:overflow-y-auto",
                // Keep the drawer in view when keyboard focus moves into it.
                "focus-within:outline focus-within:outline-2 focus-within:outline-signal"
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <SectionLabel>ENTITLEMENT DETAIL</SectionLabel>
                <button
                  type="button"
                  ref={closeBtnRef}
                  onClick={() => setSelected(null)}
                  aria-label="Close detail panel"
                  className="rounded-[2px] font-mono text-xs text-ink-soft outline-offset-4 hover:text-signal focus-visible:outline focus-visible:outline-2 focus-visible:outline-signal"
                >
                  ✕ CLOSE
                </button>
              </div>

              {selected.type === "entitlement" || selectedEntitlement ? (
                selectedEntitlement ? (
                  <>
                    <h2 className="mt-3 font-display text-2xl leading-tight">
                      {entitlementTitle(selectedEntitlement)}
                    </h2>
                    <dl className="mt-4 space-y-2.5 text-sm">
                      <Row label="Issuer">
                        {selectedEntitlement.issuer_name}{" "}
                        <span className="text-ink-soft">({selectedEntitlement.issuer_type})</span>
                      </Row>
                      <Row label="Type">{selectedEntitlement.type}</Row>
                      <Row label="Line scope">
                        {selectedEntitlement.line_item_id ?? "legacy contract scope"}
                      </Row>
                      <Row label="Status">
                        <Badge
                          tone={
                            selectedEntitlement.status === "eligible" ||
                            selectedEntitlement.status === "active"
                              ? "success"
                              : selectedEntitlement.status === "blocked"
                                ? "warning"
                                : selectedEntitlement.status === "invalid"
                                  ? "danger"
                                  : "neutral"
                          }
                        >
                          {selectedEntitlement.status.toUpperCase()}
                        </Badge>
                      </Row>
                      <Row label="Execution mode">
                        <code className="font-mono text-xs">{selectedEntitlement.execution_mode}</code>
                      </Row>
                      <Row label="Expiry">
                        {formatDateTime(selectedEntitlement.expires_at)}
                      </Row>
                      <Row label="Remedy value">
                        <MoneyText paise={selectedEntitlement.remedy_value_paise} size="sm" />
                      </Row>
                      <Row label="Est. resolution">
                        {selectedEntitlement.estimated_resolution_hours != null
                          ? `${selectedEntitlement.estimated_resolution_hours} h`
                          : "—"}
                      </Row>
                      <Row label="Required evidence">
                        {selectedEntitlement.required_evidence_types?.length
                          ? selectedEntitlement.required_evidence_types.join(", ")
                          : "—"}
                      </Row>
                      {(selectedEntitlement.fallback_to?.length ?? 0) > 0 && (
                        <Row label="Falls back to">
                          {selectedEntitlement.fallback_to!.map((fb) => (
                            <span key={fb} className="mr-2 inline-block">
                              {entitlements.find((e) => e.id === fb)?.slug ?? fb}
                            </span>
                          ))}
                        </Row>
                      )}
                      {(selectedEntitlement.blocks?.length ?? 0) > 0 && (
                        <Row label="Blocks">
                          {selectedEntitlement.blocks!
                            .map((b) => entitlements.find((e) => e.id === b)?.slug ?? b)
                            .join(", ")}
                        </Row>
                      )}
                    </dl>
                    {selectedEntitlement.activates_when?.length ? (
                      <details className="mt-4">
                        <summary className="cursor-pointer folio-label">
                          Activation predicates
                        </summary>
                        <pre className="mt-2 overflow-x-auto rounded-md border border-rule bg-paper-bright p-3 font-mono text-[0.6875rem]">
                          {prettyJson(selectedEntitlement.activates_when)}
                        </pre>
                      </details>
                    ) : null}
                  </>
                ) : (
                  <NodeSummary node={selected} />
                )
              ) : (
                <NodeSummary node={selected} />
              )}
            </aside>
          )}
        </div>
      )}

      {/* Legend */}
      <section className="rights-legend mt-12 max-w-3xl" aria-label="Edge legend">
        <SectionLabel>EDGE LEGEND</SectionLabel>
        <ul className="mt-3 divide-y divide-rule border-y border-rule">
          {EDGE_LEGEND.map(({ type, note }) => (
            <li key={type} className="flex items-baseline gap-4 py-2">
              <code className="w-36 shrink-0 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-ink">
                {type}
              </code>
              <span className="text-sm text-ink-soft">{note}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-sm leading-relaxed text-ink-soft">
          Entitlement rectangles take their color from status —{" "}
          <span className="font-medium text-success">eligible</span> outline,{" "}
          <span className="font-medium text-warning">blocked</span>,{" "}
          <span className="font-medium line-through decoration-danger">invalid</span>{" "}
          muted red,{" "}
          <span className="font-medium text-ink-soft">dormant</span> gray.
          Breach nodes are red diamonds; remedies triangles; evidence circles.
        </p>
      </section>
    </main>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] items-baseline gap-2">
      <dt className="folio-label">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function NodeSummary({ node }: { node: GraphNode }) {
  return (
    <>
      <h2 className="mt-3 font-display text-2xl leading-tight">{node.label}</h2>
      <p className="mt-1 folio-label">{node.type}</p>
      {Object.keys(node).filter((k) => !["id", "type", "label"].includes(k)).length > 0 && (
        <pre className="mt-4 overflow-x-auto rounded-md border border-rule bg-paper-bright p-3 font-mono text-[0.6875rem]">
          {prettyJson(Object.fromEntries(Object.entries(node).filter(([k]) => !["id", "type", "label"].includes(k))))}
        </pre>
      )}
      <p className="mt-3 text-sm leading-relaxed text-ink-soft">
        Full entitlement terms appear when the underlying record exposes them.
      </p>
    </>
  );
}
