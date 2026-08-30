"use client";

/**
 * Footer provenance line for the landing page. Probes GET /api/health once on
 * mount and states which payment rail is actually live — never claims Test
 * Mode when the sandbox adapter answered, and shows a neutral probing state
 * until the answer arrives (or the API is down).
 */

import { useEffect, useState } from "react";
import { apiTry } from "@/lib/api";

type Health = {
  status?: string;
  razorpay?: string;
};

export default function RailStatus() {
  const [rail, setRail] = useState<"probing" | "sandbox-adapter" | "live-test-mode" | "unknown">(
    "probing"
  );

  useEffect(() => {
    let cancelled = false;
    apiTry<Health>("/api/health").then((h) => {
      if (cancelled) return;
      const r = h?.razorpay;
      setRail(
        r === "sandbox-adapter" || r === "live-test-mode" ? r : "unknown"
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <span className="folio-label" data-rail={rail}>
      {rail === "live-test-mode"
        ? "Payments on Razorpay Test Mode · No real money moves"
        : rail === "sandbox-adapter"
          ? "Payments on the sandbox adapter · Razorpay keys not configured"
          : rail === "probing"
            ? "Payment rail · probing…"
            : "Payment rail · status unavailable"}
    </span>
  );
}
