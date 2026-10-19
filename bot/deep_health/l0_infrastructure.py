# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/l0_infrastructure.py
# claude code changed: new file — 2026-10-07 Deep Health L0 (DEEP_HEALTH_PLAN_2026-10-07.md §3 rows 1-2).
# The pre-existing System Health checks (terminal_data.get_system_health) are kept VERBATIM as Level 0 rows — they
# prove imports/config only, and say so — plus a real PostgreSQL probe (write → read → forced rollback on a TEMP
# table; no application table is touched).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from bot.deep_health.core import FAIL, INFRA, PARTIAL, PASS, Probe, health_check

_SHALLOW_MAP = {"ok": PASS, "warn": PARTIAL, "err": FAIL}


@health_check("infra_shallow", "Imports, config and data freshness (legacy System Health)", area=INFRA, level="L0",
              gates=("G1",), reuses=("bot.views.terminal_data.get_system_health",))
def shallow_checks():
    from bot.views import terminal_data
    p = Probe()
    for row in terminal_data.get_system_health()["checks"]:
        status = _SHALLOW_MAP.get(row["status"], FAIL)
        note = "import/config check only — proves the module loads, not that it computes correctly"
        p.add("G1", row["name"], status, expected="ok", observed=f"{row['status']}: {row['detail']}",
              reason=note if status == PASS else note + "; " + row["detail"])
    return p.out


class _Rollback(Exception):
    pass


@health_check("postgres", "PostgreSQL connection, write/read/rollback and schema", area=INFRA, level="L0",
              gates=("G1",))
def postgres():
    from django.db import connection, transaction
    from django.db.migrations.executor import MigrationExecutor
    p = Probe()
    with connection.cursor() as cur:
        cur.execute("SELECT version()")
        ver = cur.fetchone()[0]
    p.true("G1", "connect and query", ver.lower().startswith("postgresql"), expected="PostgreSQL server", observed=ver[:60])

    seen = None
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("CREATE TEMP TABLE deep_health_probe (v integer)")
                cur.execute("INSERT INTO deep_health_probe (v) VALUES (%s)", [4242])
                cur.execute("SELECT v FROM deep_health_probe")
                seen = cur.fetchone()[0]
            raise _Rollback()
    except _Rollback:
        pass
    p.eq("G1", "write then read inside a transaction", 4242, seen)
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass('pg_temp.deep_health_probe')")
        left = cur.fetchone()[0]
    p.eq("G1", "rollback leaves nothing behind", None, left, reason="the probe table survived a rollback")

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    p.eq("G1", "all migrations applied", 0, len(plan),
         reason="unapplied migrations: " + ", ".join(f"{m.app_label}.{m.name}" for m, _ in plan[:5]))

    from bot.journal.models import DecisionEvent, JournalRun, TradeRecord
    tables = set(connection.introspection.table_names())
    for model in (TradeRecord, JournalRun, DecisionEvent):
        p.true("G1", f"table {model._meta.db_table} present", model._meta.db_table in tables,
               expected="present", observed="present" if model._meta.db_table in tables else "missing")
    return p.out
