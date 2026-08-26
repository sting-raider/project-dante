"use client";

/**
 * RightsGraph — pure-SVG, force-free layered rights graph (plan §28).
 * Agent I. No graph libraries.
 *
 * Deterministic layered layout, top → bottom:
 *   purchase → promises → breach → entitlements → evidence → remedies
 * Edges routed orthogonally between rows with tiny mono type labels.
 * Node shapes: purchase solid square · promise outlined square ·
 *   entitlement status-colored rectangle · breach red diamond ·
 *   evidence small circle · remedy triangle.
 */

import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { palette } from "@/lib/design";

export type GraphNode = {
  id: string;
  /** purchase | promise | entitlement | breach | evidence | remedy */
  type: string;
  label: string;
  status?: string;
  [k: string]: unknown;
};

export type GraphEdge = {
  source: string;
  target: string;
  type: string;
};

export type RightsGraphProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Click / Enter on a node — drives the rights-page drawer. */
  onSelect?: (node: GraphNode) => void;
  selectedId?: string | null;
  className?: string;
  /** Overrides the auto-generated accessibility summary. */
  ariaLabel?: string;
};

const {
  ink,
  inkSoft,
  rule: RULE,
  paperBright,
  signal: SIGNAL,
  success: SUCCESS,
  warning: WARNING,
  danger: DANGER,
} = palette;

const GRAY = "#8A867C";
const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const SERIF = "'Instrument Serif', Georgia, serif";
const SANS = "'Inter', system-ui, sans-serif";

/** Entitlement rectangles color by status: outline success / warning /
 *  muted danger / gray; `active` fills success (§28 rights page). */
const ENTITLEMENT_STROKE: Record<string, string> = {
  eligible: SUCCESS,
  active: SUCCESS,
  blocked: WARNING,
  invalid: DANGER,
  dormant: GRAY,
  consumed: GRAY,
  expired: GRAY,
};

const ROW_ORDER: Record<string, number> = {
  purchase: 0,
  promise: 1,
  breach: 2,
  entitlement: 3,
  evidence: 4,
  remedy: 5,
};

const NODE_W = 148;
const NODE_H = 44;
const GAP_X = 30;
const ROW_GAP = 92;
const MIN_CANVAS_W = 760;

type Shape = "rect" | "diamond" | "circle" | "triangle";

function shapeFor(type: string): Shape {
  switch (type) {
    case "breach":
      return "diamond";
    case "evidence":
      return "circle";
    case "remedy":
      return "triangle";
    default:
      return "rect";
  }
}

function edgeColor(type: string): string {
  switch (type) {
    case "BLOCKS":
      return SIGNAL;
    case "REMEDIES":
      return SUCCESS;
    case "ACTIVATED_BY":
      return DANGER;
    case "REQUIRES":
      return WARNING;
    case "FALLBACK_TO":
    case "ISSUED_BY":
      return GRAY;
    default:
      return inkSoft;
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

type LaidNode = GraphNode & { x: number; y: number; row: number };

export default function RightsGraph({
  nodes,
  edges,
  onSelect,
  selectedId,
  className,
  ariaLabel,
}: RightsGraphProps) {
  const reduceMotion = useReducedMotion();
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const [focusedNode, setFocusedNode] = useState<string | null>(null);

  /* ---- deterministic layered layout ---- */
  const { positions, canvasWidth, canvasHeight } = useMemo(() => {
    const sorted = [...nodes].sort(
      (a, b) =>
        (ROW_ORDER[a.type] ?? 99) - (ROW_ORDER[b.type] ?? 99) ||
        a.id.localeCompare(b.id),
    );

    const byRow = new Map<number, GraphNode[]>();
    for (const n of sorted) {
      const row = ROW_ORDER[n.type] ?? 99;
      const list = byRow.get(row) ?? [];
      list.push(n);
      byRow.set(row, list);
    }

    const positions = new Map<string, LaidNode>();
    let y = 40;
    let maxExtentX = 0;
    for (const row of [...byRow.keys()].sort((a, b) => a - b)) {
      const rowNodes = byRow.get(row)!;
      const totalW = rowNodes.length * NODE_W + Math.max(0, rowNodes.length - 1) * GAP_X;
      let x = Math.max(24, Math.round((MIN_CANVAS_W - totalW) / 2));
      for (const n of rowNodes) {
        positions.set(n.id, { ...n, x, y, row });
        maxExtentX = Math.max(maxExtentX, x + NODE_W);
        x += NODE_W + GAP_X;
      }
      y += ROW_GAP;
    }

    return {
      positions,
      canvasWidth: Math.max(MIN_CANVAS_W, maxExtentX + 48),
      canvasHeight: Math.max(180, y - ROW_GAP + NODE_H + 44),
    };
  }, [nodes]);

  function edgePath(a: LaidNode, b: LaidNode): string {
    const ax = a.x + NODE_W / 2;
    const bx = b.x + NODE_W / 2;
    const ay = a.y + NODE_H;
    const by = b.y;

    if (b.row === a.row) {
      // Same-row edge: dip below both boxes.
      const midY = a.y + NODE_H + 16;
      return `M ${ax} ${ay} L ${ax} ${midY} L ${bx} ${midY} L ${bx} ${by + NODE_H}`;
    }
    if (Math.abs(ax - bx) < 6) return `M ${ax} ${ay} L ${bx} ${by}`;
    const midY = ay + (by - ay) / 2;
    return `M ${ax} ${ay} L ${ax} ${midY} L ${bx} ${midY} L ${bx} ${by}`;
  }

  const resolvedEdges = edges
    .map((e, i) => ({
      ...e,
      key: `edge-${i}`,
      from: positions.get(e.source),
      to: positions.get(e.target),
    }))
    .filter((e): e is typeof e & { from: LaidNode; to: LaidNode } => !!e.from && !!e.to);

  const countsByType = nodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.type] = (acc[n.type] ?? 0) + 1;
    return acc;
  }, {});

  const edgeCounts = edges.reduce<Record<string, number>>((acc, e) => {
    acc[e.type] = (acc[e.type] ?? 0) + 1;
    return acc;
  }, {});

  const nodeSummary =
    nodes.length === 0
      ? ""
      : Object.entries(countsByType)
          .map(([t, c]) => `${c} ${t}${c === 1 ? "" : "s"}`)
          .join(", ");
  // Edge-type summary rides in the accessible description (#15), so screen
  // reader users get the relationship vocabulary without pointing at edges.
  const edgeSummary =
    edges.length === 0
      ? ""
      : ` Relationships: ${Object.entries(edgeCounts)
          .map(([t, c]) => `${c} ${t.replace(/_/g, " ").toLowerCase()}`)
          .join(", ")}.`;

  const summary =
    ariaLabel ??
    (nodes.length === 0
      ? "Rights graph: empty"
      : `Rights graph with ${nodes.length} nodes: ${nodeSummary}.${edgeSummary}`);

  if (nodes.length === 0) {
    return (
      <div
        role="img"
        aria-label={summary}
        className={`rounded-md border border-dashed border-rule p-8 text-center font-mono text-xs uppercase tracking-[0.14em] text-ink-soft ${className ?? ""}`}
      >
        No rights graph yet — rights appear once the contract is authorized.
      </div>
    );
  }

  return (
    <figure className={className} role="group" aria-label={summary}>
      {/* role="group" keeps interactive child nodes in the accessibility tree
          (#13); role="img" would flatten them away. */}
      <svg
        viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
        className="h-auto w-full"
        style={{ minWidth: 480 }}
        role="group"
        aria-label={`Rights graph: ${nodeSummary}.${edgeSummary}`}
      >

        <defs>
          {[...new Set(edges.map((e) => e.type))].map((t) => (
            <marker
              key={t}
              id={`rg-arrow-${t.replace(/[^A-Z_]/g, "")}`}
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={6.5}
              markerHeight={6.5}
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={edgeColor(t)} />
            </marker>
          ))}
        </defs>

        {/* edges under nodes */}
        <g>
          {resolvedEdges.map(({ key, from, to, type }) => (
            <g
              key={key}
              onMouseEnter={() => setHoveredEdge(key)}
              onMouseLeave={() => setHoveredEdge(null)}
            >
              <path
                d={edgePath(from, to)}
                fill="none"
                strokeWidth={hoveredEdge === key ? 1.75 : 1}
                stroke={hoveredEdge === key ? ink : edgeColor(type)}
                strokeDasharray={
                  type === "FALLBACK_TO" || type === "BLOCKS" ? "4 3" : undefined
                }
                markerEnd={`url(#rg-arrow-${type.replace(/[^A-Z_]/g, "")})`}
              />
              <text
                x={(from.x + to.x + NODE_W) / 2}
                y={(from.y + NODE_H + to.y) / 2 - 4}
                textAnchor="middle"
                fontFamily={MONO}
                fontSize={8.5}
                letterSpacing="0.06em"
                fill={hoveredEdge === key ? ink : edgeColor(type)}
                style={{ paintOrder: "stroke", stroke: paperBright, strokeWidth: 3 }}
              >
                {type}
              </text>
            </g>
          ))}
        </g>

        {/* nodes — keyed by id+status so a status change replays the fade */}
        <g>
          {[...positions.values()].map((n) => {
            const cx = n.x + NODE_W / 2;
            const cy = n.y + NODE_H / 2;
            const selected = n.id === selectedId || focusedNode === n.id;
            const entStroke =
              n.type === "entitlement"
                ? (ENTITLEMENT_STROKE[n.status ?? "dormant"] ?? GRAY)
                : null;
            const shape = shapeFor(n.type);

            return (
              <motion.g
                key={`${n.id}:${n.status ?? ""}`}
                tabIndex={onSelect ? 0 : undefined}
                role={onSelect ? "button" : undefined}
                aria-label={`${n.type}: ${n.label}${n.status ? `, ${n.status}` : ""}${onSelect ? ". Press Enter to inspect." : ""}`}
                onClick={() => onSelect?.(n)}
                onKeyDown={(ev) => {
                  if ((ev.key === "Enter" || ev.key === " ") && onSelect) {
                    ev.preventDefault();
                    onSelect(n);
                  }
                }}
                onFocus={() => setFocusedNode(n.id)}
                onBlur={() => setFocusedNode(null)}
                initial={reduceMotion ? false : { opacity: 0.25 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.45, ease: "easeOut" }}
                style={{
                  cursor: onSelect ? "pointer" : "default",
                  // Visible keyboard focus (#13): signal ring replaces the
                  // browser default that SVG elements don't reliably paint.
                  outline: focusedNode === n.id && onSelect ? `2px solid ${SIGNAL}` : undefined,
                  outlineOffset: 4,
                }}
              >
                <title>{`${n.type.toUpperCase()} — ${n.label}${n.status ? ` (${n.status})` : ""}`}</title>

                {(selected || (focusedNode === n.id && onSelect)) && (
                  <rect
                    x={n.x - 6}
                    y={n.y - 6}
                    width={
                      shape === "diamond" ? NODE_W + 12 : NODE_W + 12
                    }
                    height={shape === "diamond" ? NODE_H + 26 : NODE_H + 12}
                    fill="none"
                    stroke={focusedNode === n.id ? SIGNAL : ink}
                    strokeWidth={focusedNode === n.id ? 1.75 : 1}
                    strokeDasharray={focusedNode === n.id ? undefined : "3 3"}
                    rx={2}
                  />
                )}

                {shape === "diamond" ? (
                  <polygon
                    points={`${cx},${cy - 26} ${cx + 26},${cy} ${cx},${cy + 26} ${cx - 26},${cy}`}
                    fill={paperBright}
                    stroke={SIGNAL}
                    strokeWidth={n.id === selectedId ? 2.5 : 1.6}
                  />
                ) : shape === "circle" ? (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={11}
                    fill={RULE}
                    stroke={n.id === selectedId ? ink : inkSoft}
                    strokeWidth={n.id === selectedId ? 2.2 : 1}
                  />
                ) : shape === "triangle" ? (
                  <polygon
                    points={`${cx},${cy - 16} ${cx + 15},${cy + 12} ${cx - 15},${cy + 12}`}
                    fill={paperBright}
                    stroke={SUCCESS}
                    strokeWidth={n.id === selectedId ? 2.4 : 1.4}
                  />
                ) : (
                  <rect
                    x={n.x}
                    y={n.y}
                    width={NODE_W}
                    height={NODE_H}
                    rx={2}
                    fill={entStroke ? paperBright : n.type === "purchase" ? ink : paperBright}
                    stroke={entStroke ?? (n.type === "promise" ? inkSoft : ink)}
                    strokeWidth={n.id === selectedId ? 2.4 : 1.2}
                    strokeDasharray={
                      entStroke &&
                      ((n.status ?? "") === "dormant" || (n.status ?? "") === "expired")
                        ? "3 2"
                        : undefined
                    }
                  />
                )}

                <text
                  x={cx}
                  y={
                    shape === "circle"
                      ? cy + 25
                      : shape === "triangle"
                        ? cy + 27
                        : shape === "diamond"
                          ? cy + 40
                          : cy - 1
                  }
                  textAnchor="middle"
                  fontFamily={n.type === "purchase" ? SERIF : SANS}
                  fontSize={shape === "rect" ? 11.5 : 10.5}
                  fontWeight={n.type === "purchase" ? 600 : 400}
                  fill={n.type === "purchase" ? paperBright : ink}
                  style={{ pointerEvents: "none" }}
                >
                  {truncate(n.label, shape === "rect" ? 21 : 26)}
                </text>

                {(shape === "rect" || shape === "triangle") && (
                  <text
                    x={cx}
                    y={shape === "rect" ? cy + 14 : cy + 40}
                    textAnchor="middle"
                    fontFamily={MONO}
                    fontSize={7.5}
                    letterSpacing="0.09em"
                    fill={entStroke ?? (n.type === "purchase" ? RULE : GRAY)}
                    style={{ pointerEvents: "none" }}
                  >
                    {truncate(
                      (n.status ?? n.type).toUpperCase(),
                      shape === "rect" ? 18 : 24,
                    )}
                  </text>
                )}
              </motion.g>
            );
          })}
        </g>
      </svg>

      <figcaption className="sr-only">{summary}</figcaption>
    </figure>
  );
}
