"""Daemon d'ingestion — l'UNIQUE processus écrivain SQLite (plan de dev §1.1).

Lot 0 : une seule tâche (fetch_firms toutes les 15 min).
Les tâches des Lots 1+ (météo, sécheresse, backfill, archive_sweep, process_cycle)
s'ajouteront ici, jamais dans un cron séparé.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler

from vigifeu.ingest.firms import fetch_cycle
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources

log = logging.getLogger("vigifeu")


def _ping_healthcheck(slug_env: str) -> None:
    """Ping healthchecks.io si l'URL est configurée (monitoring, plan §1.1)."""
    url = os.environ.get(slug_env)
    if not url:
        return
    try:
        httpx.get(url, timeout=10)
    except httpx.HTTPError:
        log.warning("ping healthcheck échoué (%s)", slug_env)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,  # journald récupère stdout sous systemd
    )
    config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))
    conn = connect(config["general"]["db_path"])
    applied = migrate(conn)
    if applied:
        log.info("migrations appliquées: %s", applied)
    sync_satellite_sources(conn, config)

    def job_fetch_firms() -> None:
        results = fetch_cycle(conn, config)
        ok = [r for r in results if r["status"] == "ok"]
        err = [r for r in results if r["status"] == "error"]
        total_new = sum(r.get("n_new", 0) for r in ok)
        log.info(
            "fetch_firms: %d requêtes ok, %d en erreur, %d hotspots nouveaux",
            len(ok), len(err), total_new,
        )
        for r in err:
            log.error("fetch_firms %s %s: %s", r["source"], r["day"], r["error"])
        if not err:
            _ping_healthcheck("HEALTHCHECK_FIRMS_URL")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        job_fetch_firms,
        "interval",
        minutes=config["firms"]["fetch_interval_min"],
        id="fetch_firms",
        max_instances=1,        # jamais deux cycles concurrents
        coalesce=True,          # rattrapages fusionnés après une pause
        next_run_time=None,
    )

    def _shutdown(signum, frame):  # noqa: ARG001
        log.info("arrêt demandé (signal %s)", signum)
        scheduler.shutdown(wait=False)
        conn.close()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("démarrage — premier cycle immédiat, puis toutes les %d min",
             config["firms"]["fetch_interval_min"])
    job_fetch_firms()  # premier cycle sans attendre l'intervalle
    scheduler.get_job("fetch_firms").modify(next_run_time=None)
    scheduler.start()


if __name__ == "__main__":
    main()
