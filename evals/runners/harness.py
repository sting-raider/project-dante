"""Shared harness for Project Dante eval runners.

Provides dataset loading, module-availability probing, value matching
semantics, report writing, and summary metrics. Runners import from here;
nothing in here imports project_dante directly — each runner declares its
own dependencies so a missing backend module degrades to SKIP gracefully.

Exit-code contract: 0 iff all thresholds met AND at least one case ran
(NOT_RUN_YET runs exit non-zero so CI cannot silently pass on skip).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATASETS = REPO_ROOT / "evals" / "datasets"
REPORTS = REPO_ROOT / "evals" / "reports"
FIXTURES = REPO_ROOT / "fixtures"

# Deterministic rules path for every runner (plan §51: deterministic code owns authority).
os.environ.setdefault("LLM_PROVIDER", "")
os.environ.setdefault("DANTE_STORE_PATH", str(REPO_ROOT / "evals" / "reports" / ".dante-eval-store.json"))

WEEKDAY_IDX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


# ------------------------------------------------------------------ loading


def load_dataset(name: str) -> dict[str, Any]:
    path = DATASETS / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require_modules(import_map: dict[str, list[str]]) -> tuple[bool, str | None]:
    """Probe imports. Returns (ok, missing_description)."""
    missing = []
    for module_name, attr_names in import_map.items():
        try:
            mod = __import__(module_name, fromlist=attr_names)
            for attr in attr_names:
                if not hasattr(mod, attr):
                    missing.append(f"{module_name}.{attr}")
        except Exception as exc:  # ImportError or broken transitive import
            missing.append(f"{module_name} ({exc.__class__.__name__}: {exc})")
    if missing:
        return False, "; ".join(missing)
    return True, None


# ------------------------------------------------------------ date handling


def resolve_date_placeholder(value: Any) -> Any:
    """Resolve <NEXT_THURSDAY>/<TOMORROW>/<NEXT_FRIDAY> placeholders to ISO dates."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    today = datetime.now(UTC)
    if v == "<TOMORROW>":
        from datetime import timedelta

        return (today + timedelta(days=1)).date().isoformat()
    if v.startswith("<NEXT_"):
        day = v.removeprefix("<NEXT_").removesuffix(">").lower()
        if day in WEEKDAY_IDX:
            target = WEEKDAY_IDX[day]
            days_ahead = (target - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # said-on-the-day means next week (compiler rule)
            from datetime import timedelta

            return (today + timedelta(days=days_ahead)).date().isoformat()
    return value


def normalize_scalar(v: Any) -> Any:
    """Case-insensitive scalar normalization for comparisons."""
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    return str(v).strip().lower()


def _sing(word: Any) -> str:
    """Crude singular form of a category word ('laptops'->'laptop')."""
    s = str(word or "").strip().lower()
    if s.endswith("ies"):
        return s[:-3] + "y"
    if s.endswith("s") and not s.endswith("ss"):
        return s[:-1]
    return s


_CATEGORY_EQUIV = {
    # compiler token -> acceptable dataset values
    "headphone": {"headphones"},
    "earbud": {"headphones", "earbuds"},  # earbuds ARE headphone-category products
    "router": {"routers"},
    "laptop": {"laptops"},
    "charger": {"chargers-cables", "chargers", "cables", "charger"},
    "cable": {"chargers-cables", "chargers", "cables", "cable"},
    "keyboard": {"keyboards"},
    "mouse": {"mice", "mouse"},
    "monitor": {"monitors"},
    "phone": {"phones"},
}


def _cat_equivalent(actual: Any, expected: Any) -> bool:
    a = _sing(str(actual or "").strip().lower())
    e = _sing(str(expected or "").strip().lower())
    if a == e:
        return True
    return e in _CATEGORY_EQUIV.get(a, set()) or a in _CATEGORY_EQUIV.get(e, set())


def scalars_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    return normalize_scalar(a) == normalize_scalar(b)


def constraint_satisfied(actual_constraints: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    """Is this expected {key, op, value} present among actual hard constraints?"""
    exp_key = expected["key"]
    exp_op = expected.get("op", "eq")
    exp_value = expected["value"]
    for c in actual_constraints:
        # Category equivalence: compiler emits singular ("laptop"), catalog uses
        # plural ("laptops"); earbuds map onto the headphones category.
        key_ok = c.get("key") == exp_key or (
            exp_key == "category"
            and c.get("key") == "category"
            and {_sing(c.get("value")), _sing(exp_value)}
            & {"headphones", "earbuds"} != set()
            and _cat_equivalent(c.get("value"), exp_value)
        )
        if not key_ok:
            continue
        if c.get("op", "eq") != exp_op and not (exp_key == "category"):
            continue
        av, ev = c.get("value"), exp_value
        if exp_op == "in":
            # expected value may be a list of acceptable values; match any.
            # If the ACTUAL value is itself a list (e.g. brand sets), overlap counts.
            if isinstance(av, (list, tuple)) and isinstance(ev, (list, tuple)):
                a_norm = {normalize_scalar(x) for x in av}
                e_norm = {normalize_scalar(x) for x in ev}
                if a_norm & e_norm:
                    return True
            ev_list = ev if isinstance(ev, list) else [ev]
            if any(_cat_equivalent(av, x) or scalars_equal(av, x) for x in ev_list):
                return True
        elif exp_op in ("lte", "lt", "gte", "gt"):
            # Dates compare as ISO strings; numbers as floats.
            a_str, e_str = str(av), str(ev)
            is_date = _ISO_DATE.fullmatch(a_str) and _ISO_DATE.fullmatch(e_str)
            if is_date:
                if {"lte": a_str <= e_str, "lt": a_str < e_str, "gte": a_str >= e_str, "gt": a_str > e_str}[exp_op]:
                    return True
                continue
            try:
                avn = float(a_str.replace(",", ""))
                evn = float(e_str.replace(",", ""))
            except (TypeError, ValueError):
                continue
            if {"lte": avn <= evn, "lt": avn < evn, "gte": avn >= evn, "gt": avn > evn}[exp_op]:
                return True
        else:
            if exp_key == "category" and _cat_equivalent(av, ev):
                return True
            if scalars_equal(av, ev):
                return True
    return False


# ------------------------------------------------------------------ reports


def write_report(run_name: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    payload.setdefault("generated_at", datetime.now(UTC).isoformat())
    json_path = REPORTS / f"{run_name}.json"
    md_path = REPORTS / f"{run_name}.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    lines: list[str] = [f"# Eval report: {run_name}", ""]
    status = payload.get("status", "?")
    lines.append(f"**Status:** `{status}`  ·  **Generated:** {payload['generated_at']}")
    if payload.get("skipped_reason"):
        lines.append(f"**Skipped reason:** {payload['skipped_reason']}")
    lines.append("")
    metrics = payload.get("metrics") or {}
    if metrics:
        lines.append("## Metrics")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        for k, v in metrics.items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
    failures = payload.get("failures") or []
    lines.append(f"## Failures ({len(failures)})")
    lines.append("")
    if failures:
        for fl in failures:
            cid = fl.get("case_id", fl.get("id", "?"))
            why = fl.get("reason") or json.dumps(fl, ensure_ascii=False)[:400]
            lines.append(f"- **{cid}** — {why}")
    else:
        lines.append("None.")
    lines.append("")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return json_path, md_path


def threshold_check(metrics: dict[str, Any], thresholds: list[tuple[str, Callable[[Any], bool]]]) -> tuple[bool, list[str]]:
    ok = True
    msgs = []
    for name, fn in thresholds:
        val = metrics.get(name)
        passed = val is not None and fn(val)
        msgs.append(f"{name}={val} ({'PASS' if passed else 'FAIL'}, required: {_threshold_label(fn)})")
        if not passed:
            ok = False
    return ok, msgs


def _threshold_label(fn: Callable[[Any], bool]) -> str:
    return getattr(fn, "threshold_label", "custom")


def exact(n: Any) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        return v == n

    check.threshold_label = f"== {n}"
    return check


def at_least(n: float) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        try:
            return float(v) >= n
        except (TypeError, ValueError):
            return False

    check.threshold_label = f">= {n}"
    return check


def print_case(cid: str, passed: bool, detail: str = "") -> None:
    mark = "PASS" if passed else "FAIL"
    line = f"[{mark}] {cid}"
    if detail:
        line += f" — {detail}"
    print(line)


def summarize(results: list[dict[str, Any]]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"] and not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))
    print(f"\n=== {total} cases: {passed} pass, {failed} fail, {skipped} skipped ===")


class Timer:
    def __enter__(self) -> "Timer":
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.seconds = time.monotonic() - self.t0
