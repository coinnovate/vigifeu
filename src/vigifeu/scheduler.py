"""Daemon d'ingestion — l'UNIQUE processus écrivain SQLite (plan de dev §1.1).

Tâches (Spec 02 §2) portées par ce seul daemon (jamais un cron séparé, pour
garantir l'unicité de l'écrivain sous WAL) :

- fetch_firms   (15 min)  : collecte FIRMS puis construction des passages ;
- backfill      (horaire) : rattrapage des jours à trous jusqu'à J-7 ;
- gap_check     (horaire) : alerte si trou de collecte > seuil (Spec 02 §9) ;
- archive_sweep (quotidien, nuit) : export Parquet + purge de la fenêtre glissante.

Chaque tâche pingue son healthcheck sur succès (dead-man switch). Le moteur
d'interprétation (clustering, qualification…) s'ajoutera ici au Lot 2.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from vigifeu.engine.overpass import build_overpasses
from vigifeu.ingest.firms import fetch_cycle, fetch_firms_backfill
from vigifeu.model.archive import archive_sweep
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources
from vigifeu.model.monitoring import check_collection_gap, ping_healthcheck

log = logging.getLogger("vigifeu")


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
        # Étape 2 du cycle (Spec 02 §3) : rattacher les nouveaux hotspots aux passages.
        if total_new:
            ov = build_overpasses(conn, config)
            log.info(
                "overpass: %d rattachés, %d nouveaux passages",
                ov["n_attached"], ov["n_new_overpasses"],
            )
        if not err:
            ping_healthcheck(os.environ.get("HEALTHCHECK_FIRMS_URL"))

    def job_backfill() -> None:
        results = fetch_firms_backfill(conn, config)
        rattrapes = [r for r in results if r["status"] == "ok"]
        if results:
            log.info("backfill: %d/%d jours rattrapés", len(rattrapes), len(results))
        ping_healthcheck(os.environ.get("HEALTHCHECK_BACKFILL_URL"))

    def job_gap_check() -> None:
        r = check_collection_gap(conn, config)
        if r["alert"]:
            log.error("ALERTE MONITORING: %s", r["message"])
            ping_healthcheck(os.environ.get("HEALTHCHECK_COLLECTION_URL"), ok=False)
        else:
            ping_healthcheck(os.environ.get("HEALTHCHECK_COLLECTION_URL"))

    def job_archive() -> None:
        res = archive_sweep(conn, config)
        log.info(
            "archive_sweep: %d exportés, %d purgés (%d protégés), %d runs purgés",
            res["exported_hotspots"], res["purged_hotspots"],
            res["protected_hotspots"], res["purged_runs"],
        )
        ping_healthcheck(os.environ.get("HEALTHCHECK_ARCHIVE_URL"))

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        job_fetch_firms, "interval",
        minutes=config["firms"]["fetch_interval_min"],
        id="fetch_firms", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_backfill, "interval", hours=1,
        id="backfill", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_gap_check, "interval", hours=1,
        id="gap_check", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_archive, "cron", hour=3, minute=30,   # nuit, hors pics de collecte
        id="archive_sweep", max_instances=1, coalesce=True,
    )

    def _shutdown(signum, frame):  # noqa: ARG001
        log.info("arrêt demandé (signal %s)", signum)
        scheduler.shutdown(wait=False)
        conn.close()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info(
        "démarrage — fetch immédiat puis toutes les %d min ; backfill+gap horaires ; archive 03h30",
        config["firms"]["fetch_interval_min"],
    )
    job_fetch_firms()  # premier cycle sans attendre l'intervalle
    scheduler.start()


if __name__ == "__main__":
    main()
