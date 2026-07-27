"""CLI Vigifeu (Lot 0).

  vigifeu init                    — crée/migre la base, synchronise les sources
  vigifeu fetch [YYYY-MM-DD]      — ingère un jour (défaut : aujourd'hui) pour tous les satellites
  vigifeu backfill A B            — ingère l'intervalle de jours [A, B] (constitution de fixtures)
  vigifeu backfill-gaps           — rattrape les jours à trous (runs en échec) jusqu'à J-7
  vigifeu latence                 — statistiques de latence NRT (le jalon L0)
  vigifeu runs [N]                — derniers runs d'ingestion (défaut : 20)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

from vigifeu.ingest.firms import fetch_firms_backfill, ingest_day
from vigifeu.model.db import (
    active_sources,
    connect,
    load_config,
    migrate,
    sync_satellite_sources,
)


def _open():
    config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))
    conn = connect(config["general"]["db_path"])
    migrate(conn)
    sync_satellite_sources(conn, config)
    return conn, config


def cmd_init() -> None:
    conn, config = _open()
    n = conn.execute("SELECT COUNT(*) AS n FROM satellite_source WHERE active=1").fetchone()["n"]
    print(f"base prête : {config['general']['db_path']} — {n} sources actives")


def cmd_fetch(day_str: str | None) -> None:
    conn, config = _open()
    day = date.fromisoformat(day_str) if day_str else datetime.now(UTC).date()
    for src in active_sources(conn):
        r = ingest_day(conn, config, src, day)
        print(f"{src['code']:>18} {day}: {r}")


def cmd_backfill(start_str: str, end_str: str) -> None:
    conn, config = _open()
    day, end = date.fromisoformat(start_str), date.fromisoformat(end_str)
    while day <= end:
        for src in active_sources(conn):
            r = ingest_day(conn, config, src, day)
            print(f"{src['code']:>18} {day}: {r}")
        day += timedelta(days=1)


def cmd_backfill_gaps() -> None:
    conn, config = _open()
    results = fetch_firms_backfill(conn, config)
    if not results:
        print("aucun trou à rattraper")
        return
    for r in results:
        print(f"{r['source']:>18} {r['day']}: {r}")


def cmd_latence() -> None:
    conn, _ = _open()
    rows = conn.execute(
        """SELECT source, COUNT(*) AS n,
                  ROUND(MIN(latence_h), 2) AS min_h,
                  ROUND(AVG(latence_h), 2) AS moy_h,
                  ROUND(MAX(latence_h), 2) AS max_h
           FROM v_latence_nrt GROUP BY source ORDER BY source"""
    ).fetchall()
    if not rows:
        print("aucun hotspot ingéré pour l'instant")
        return
    print(f"{'source':>18} {'n':>7} {'min (h)':>8} {'moy (h)':>8} {'max (h)':>8}")
    for r in rows:
        print(f"{r['source']:>18} {r['n']:>7} {r['min_h']:>8} {r['moy_h']:>8} {r['max_h']:>8}")
    print("\nnote : la latence n'est significative que pour les hotspots ingérés en continu")
    print("(un backfill d'archive mesure le backfill, pas le NRT)")


def cmd_runs(n: int) -> None:
    conn, _ = _open()
    for r in conn.execute(
        "SELECT * FROM ingestion_run ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall():
        print(
            f"#{r['id']} {r['started_at']} {r['source']:<24} {r['status']:<7}"
            f" rows={r['n_rows']} new={r['n_new']}"
            + (f" ERR: {r['error_text']}" if r["error_text"] else "")
        )


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd, rest = args[0], args[1:]
    match cmd:
        case "init":
            cmd_init()
        case "fetch":
            cmd_fetch(rest[0] if rest else None)
        case "backfill":
            cmd_backfill(rest[0], rest[1])
        case "backfill-gaps":
            cmd_backfill_gaps()
        case "latence":
            cmd_latence()
        case "runs":
            cmd_runs(int(rest[0]) if rest else 20)
        case _:
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    main()
