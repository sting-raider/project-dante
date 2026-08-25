"use client";

import { apiTry } from "@/lib/api";
import { useEffect, useState } from "react";

type Analytics = {
  total_products?: number;
  ai_transactable_rate?: number;
  evaluated_intents?: number;
  blocker_distribution?: Record<string, number>;
};

type Health = {
  status?: string;
  razorpay?: string;
  demo_mode?: boolean;
};

/**
 * Live stat strip — fetches /api/merchant/analytics and renders graceful
 * em-dashes when the API is down (landing page requirement). Client-side so
 * the page shell stays static.
 */
export default function StatStrip() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [a, h] = await Promise.all([
        apiTry<Analytics>("/api/merchant/analytics"),
        apiTry<Health>("/api/health"),
      ]);
      if (!cancelled) {
        setAnalytics(a);
        setHealth(h);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const items: Array<[string, string]> = [
    ["Catalog SKUs", analytics?.total_products != null ? String(analytics.total_products) : "—"],
    [
      "AI-transactable",
      analytics?.ai_transactable_rate != null
        ? `${Math.round(analytics.ai_transactable_rate * 100)}%`
        : "—",
    ],
    ["Intents evaluated", analytics?.evaluated_intents != null ? String(analytics.evaluated_intents) : "—"],
    ["API", health ? "online" : "offline"],
    ["Rail", health?.razorpay === "sandbox-adapter" ? "sandbox" : health ? "live · test mode" : "—"],
  ];

  return (
    <dl className="grid grid-cols-2 gap-px border border-rule bg-rule md:grid-cols-5">
      {items.map(([label, value]) => (
        <div key={label} className="bg-paper px-4 py-4">
          <dd className="tabular font-display text-3xl leading-none text-ink">{value}</dd>
          <dt className="folio-label mt-2">{label}</dt>
        </div>
      ))}
    </dl>
  );
}
