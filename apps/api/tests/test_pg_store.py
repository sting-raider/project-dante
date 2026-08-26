"""Postgres store backend tests (persistence specialist, plan §11).

Skips honestly when no Postgres is reachable:

- ``DATABASE_URL`` (or ``DANTE_TEST_DATABASE_URL``) set -> connect to it;
- otherwise, if the Docker CLI works, try to start the repo's
  docker-compose postgres service (dante/dante/dante on localhost:5433)
  and tear it down afterwards;
- otherwise SKIP with a clear reason. Skips are reported as skips, not
  passes.

Every test runs against a throwaway schema namespace: the store is pointed
at a dedicated database whenever possible and ``reset()`` is used between
tests; a unique-prefix guard keeps concurrent CI runs from colliding.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid

import pytest
from project_dante.db.pg_store import PostgresStore, PostgresStoreError  # noqa: I001

COMPOSE_DEFAULT_DSN = "postgresql://dante:dante@localhost:5433/dante"
CONTAINER_UP_TIMEOUT_S = 90


def _try_connect(dsn: str) -> bool:
    """True if psycopg can reach the DSN right now."""
    import psycopg

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _docker_engine_up() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _start_compose_postgres() -> str | None:
    """Best-effort: bring up the compose postgres, return its DSN or None."""
    if not (_docker_available() and _docker_engine_up()):
        return None
    repo_root = os.environ.get("DANTE_REPO_ROOT")
    if not repo_root:
        # tests/.. is apps/api; repo root is two levels up.
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    compose = os.path.join(repo_root, "docker-compose.yml")
    if not os.path.exists(compose):
        return None
    try:
        subprocess.run(
            [
                "docker", "compose", "-f", compose,
                "up", "-d", "postgres",
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    deadline = time.monotonic() + CONTAINER_UP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _try_connect(COMPOSE_DEFAULT_DSN):
            return COMPOSE_DEFAULT_DSN
        time.sleep(2)
    return None


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    """Resolve a usable Postgres DSN or skip with a clear reason."""
    dsn = (
        os.environ.get("DANTE_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    source = "env"
    if not dsn:
        dsn = _start_compose_postgres()
        source = "docker-compose"
    if not dsn:
        pytest.skip(
            "Postgres backend not exercised: no DATABASE_URL / "
            "DANTE_TEST_DATABASE_URL set and docker compose postgres "
            f"({COMPOSE_DEFAULT_DSN}) unavailable"
        )
    if not _try_connect(dsn):
        if source == "docker-compose":
            pytest.skip(
                f"docker compose postgres started but not reachable at {dsn}"
            )
        pytest.skip(f"DATABASE_URL set but Postgres unreachable at {dsn}")
    return dsn


@pytest.fixture()
def store(pg_dsn: str) -> PostgresStore:
    s = PostgresStore(pg_dsn)
    s.ensure_schema()
    s.reset()
    yield s
    s.reset()
    s.close()


def _rec(rid: str, rtype: str, **fields: object) -> dict[str, object]:
    return {"_type": rtype, "id": rid, **fields}


# ---------------------------------------------------------------- roundtrip


def test_put_get_roundtrip(store: PostgresStore):
    rec = _rec(
        "con_pg_1", "contract",
        amount=1_149_900,
        status="PAID",
        tags=["priority", "verified"],
        meta={"nested": {"key": "value"}},
    )
    out = store.put(rec)
    assert out == rec  # put returns a copy of what was stored

    got = store.get("con_pg_1")
    assert got == rec  # exact shape incl. ints, lists, nested dicts
    assert got is not None and got["amount"] == 1_149_900  # paise survive
    assert got["_type"] == "contract"


def test_get_missing_returns_none(store: PostgresStore):
    assert store.get("nope_missing") is None


def test_put_overwrites_same_id(store: PostgresStore):
    store.put(_rec("off_pg_1", "offer", price=100))
    store.put(_rec("off_pg_1", "offer", price=200))
    got = store.get("off_pg_1")
    assert got == {"_type": "offer", "id": "off_pg_1", "price": 200}
    assert store.count("offer") == 1


# -------------------------------------------------------------------- update


def test_update_merges_fields(store: PostgresStore):
    store.put(_rec("pr_pg_1", "promise", status="FROZEN", amount=500))
    updated = store.update("pr_pg_1", status="BREACHED", note="late shipment")
    assert updated is not None
    assert updated["status"] == "BREACHED"
    assert updated["amount"] == 500  # untouched field survives merge
    assert updated["note"] == "late shipment"
    # persisted
    assert store.get("pr_pg_1")["status"] == "BREACHED"  # type: ignore[index]


def test_update_missing_returns_none(store: PostgresStore):
    assert store.update("ghost_id", status="X") is None


def test_delete(store: PostgresStore):
    store.put(_rec("ent_pg_1", "entitlement", active=True))
    assert store.delete("ent_pg_1") is True
    assert store.get("ent_pg_1") is None
    assert store.delete("ent_pg_1") is False  # second delete reports False


# ------------------------------------------------------------- list / find


def test_list_filters_by_type(store: PostgresStore):
    store.put(_rec("a", "fact", n=1))
    store.put(_rec("b", "fact", n=2))
    store.put(_rec("c", "breach", n=3))
    facts = store.list("fact")
    assert sorted(r["id"] for r in facts) == ["a", "b"]
    all_ids = {r["id"] for r in store.list()}
    assert all_ids == {"a", "b", "c"}


def test_find_by_scalar_fields(store: PostgresStore):
    store.put(_rec("rzo_1", "razorpay_order", contract_id="con_x", amount=1000))
    store.put(_rec("rzo_2", "razorpay_order", contract_id="con_y", amount=1000))
    store.put(_rec("rzo_3", "razorpay_order", contract_id="con_x", amount=2500))

    hits = store.find("razorpay_order", contract_id="con_x")
    assert {h["id"] for h in hits} == {"rzo_1", "rzo_3"}

    hits = store.find("razorpay_order", contract_id="con_x", amount=2500)
    assert {h["id"] for h in hits} == {"rzo_3"}


def test_find_matches_json_store_semantics(store: PostgresStore):
    """find() must agree with db.store.Store for non-scalar shapes."""
    from project_dante.db.store import Store

    records = [
        _rec("m1", "money_action", contract_id="c1", payload={"k": [1, 2]}),
        _rec("m2", "money_action", contract_id="c1", payload=None),
        _rec("m3", "money_action", contract_id="c2"),
        _rec("m4", "money_action"),
    ]
    json_store = Store(os.path.join(os.path.dirname(__file__), "_pg_equiv.json"))
    try:
        json_store.reset()
        for r in records:
            json_store.put(r)
            store.put(r)

        cases: list[tuple[dict[str, object], set[str]]] = [
            ({"contract_id": "c1"}, {"m1", "m2"}),
            ({"payload": None}, {"m2"}),  # None equality — Python fallback path
            ({"contract_id": "missing"}, set()),
        ]
        for fields, expected in cases:
            pg_ids = {r["id"] for r in store.find("money_action", **fields)}
            js_ids = {r["id"] for r in json_store.find("money_action", **fields)}
            assert pg_ids == js_ids == expected, (fields, pg_ids, js_ids)
    finally:
        json_store.reset()


def test_find_no_fields_lists_type(store: PostgresStore):
    store.put(_rec("x1", "webhook_event", handled=True))
    store.put(_rec("x2", "intent", handled=False))
    hits = store.find("webhook_event")
    assert [h["id"] for h in hits] == ["x1"]


def test_find_one(store: PostgresStore):
    store.put(_rec("pd_1", "policy_decision", decision="ALLOW"))
    store.put(_rec("pd_2", "policy_decision", decision="DENY"))
    hit = store.find_one("policy_decision", decision="DENY")
    assert hit is not None and hit["id"] == "pd_2"
    assert store.find_one("policy_decision", decision="NOPE") is None


# ------------------------------------------------------------------- count


def test_count(store: PostgresStore):
    assert store.count() == 0
    for i in range(3):
        store.put(_rec(f"ev_{i}", "evidence", i=i))
    store.put(_rec("br_9", "breach"))
    assert store.count() == 4
    assert store.count("evidence") == 3
    assert store.count("breach") == 1
    assert store.count("agent_run") == 0


# ------------------------------------------------------------------- reset


def test_reset_wipes_and_reports(store: PostgresStore):
    store.put(_rec("mi_1", "merchant_insight"))
    store.put(_rec("mi_2", "merchant_insight"))
    removed = store.reset()
    assert removed == 2
    assert store.count() == 0
    assert store.get("mi_1") is None


# ------------------------------------------------------------- concurrency


def test_concurrent_puts_smoke(store: PostgresStore):
    """10 threads x 10 puts: all land, none lost."""
    n_threads, per_thread = 10, 10
    errors: list[Exception] = []

    def worker(t: int) -> None:
        try:
            for i in range(per_thread):
                rid = f"run_{t}_{i}"
                store.put(_rec(rid, "agent_run", t=t, i=i))
        except Exception as exc:  # noqa: BLE001 - collected below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    assert not errors, errors[:3]
    ids = {r["id"] for r in store.list("agent_run")}
    assert len(ids) == n_threads * per_thread


# ------------------------------------------------------------ construction


def test_requires_dsn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(PostgresStoreError):
        PostgresStore(None)


def test_bad_dsn_raises_clear_error():
    dead = PostgresStore(
        "postgresql://dante:definitely-not-the-password@"
        "localhost:59999/does_not_exist"
    )
    with pytest.raises(PostgresStoreError, match="cannot connect"):
        dead.get("anything")


def test_unique_prefix_isolation(pg_dsn: str):  # noqa: ARG001 - dsn gate only
    """Two stores on one DB see each other's committed rows by design."""
    marker = uuid.uuid4().hex
    a = PostgresStore(pg_dsn)
    b = PostgresStore(pg_dsn)
    try:
        a.ensure_schema()
        b.ensure_schema()
        a.put(_rec(f"fact_shared_{marker}", "fact", via="a"))
        assert b.get(f"fact_shared_{marker}")["via"] == "a"  # type: ignore[index]
    finally:
        a.delete(f"fact_shared_{marker}")
        a.close()
        b.close()


# ------------------------------------------------- no-DB unit coverage
#
# The find() SQL/Python split is the one piece of subtle logic in the
# backend; exercise it directly so it is covered even on machines with no
# Postgres (where the integration tests above skip).


def test_sql_comparable_scalar_routing():
    f = PostgresStore._sql_comparable
    # Lossless scalars -> SQL fast path.
    assert f("con_x") == "con_x"
    assert f(42) == "42"
    assert f(0) == "0"
    assert f(True) == "true"
    assert f(False) == "false"
    # Shapes where ->> text comparison would be wrong/inexact -> Python path.
    assert f(None) is None
    assert f(1.5) is None          # float representation drift risk
    assert f(["a", "b"]) is None   # containers
    assert f({"k": 1}) is None


def test_find_plan_splits_sql_and_python_filters():
    """Mixed scalar+None fields must produce SQL for scalars only."""
    where, params, py_checks = PostgresStore._find_plan(
        "contract", {"status": "PAID", "payload": None, "amount": 2500}
    )
    assert where == [
        "record_type = %s",
        "payload->>%s = %s",
        "payload->>%s = %s",
    ]
    # Scalars ride the parameter list; None is deferred to Python matching.
    assert ("status", "PAID") in params
    assert ("amount", "2500") in params
    assert py_checks == [("payload", None)]


def test_find_plan_all_python_when_no_scalars():
    where, params, py_checks = PostgresStore._find_plan(
        "money_action", {"payload": [1, 2]}
    )
    assert where == ["record_type = %s"]
    assert params == [("record_type", "money_action")]
    assert py_checks == [("payload", [1, 2])]

