/**
 * Minimal class combiner (no clsx dependency): filters falsy, joins.
 * Signature-compatible with clsx for the string/record cases we use.
 */
export function cn(
  ...parts: Array<string | false | null | undefined | Record<string, boolean | undefined>>
): string {
  const out: string[] = [];
  for (const part of parts) {
    if (!part) continue;
    if (typeof part === "string") {
      out.push(part);
    } else {
      for (const [k, v] of Object.entries(part)) if (v) out.push(k);
    }
  }
  return out.join(" ");
}
