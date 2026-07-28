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
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from vigifeu.engine import geo
from vigifeu.engine.cluster import apply_lifecycle
from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.commune_context import refresh_commune_context
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.regen import enqueue_fire_update
from vigifeu.engine.relations import fire_footprint_l93
from vigifeu.engine.wind import recompute_direction_vent
from vigifeu.ingest.firms import fetch_cycle, fetch_firms_backfill
from vigifeu.ingest.weather import fetch_weather_obs
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
        # Étapes 2→7 du cycle (Spec 02 §3) : passages puis moteur d'interprétation.
        if total_new:
            ov = build_overpasses(conn, config)
            log.info(
                "overpass: %d rattachés, %d nouveaux passages",
                ov["n_attached"], ov["n_new_overpasses"],
            )
            res = process_cycle(conn, config)
            log.info(
                "moteur: %d créés, %d rattachés, %d fusions, %d reprises, "
                "%d requalifiés, %d versions, %d sources promues",
                res["created"], res["attached"], res["merged"], res["reprises"],
                res["requalified"], res["versioned"], res["promoted"],
            )
        if not err:
            ping_healthcheck(os.environ.get("HEALTHCHECK_FIRMS_URL"))

    def job_lifecycle() -> None:
        # Passe horaire (Spec 02 §4.5) : transitions des feux sans nouveauté
        # (actif → plus_detecte → archive) contre l'heure courante.
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = apply_lifecycle(conn, config, clock=now)
        if res["to_plus_detecte"] or res["to_archive"]:
            log.info(
                "cycle de vie: %d → plus_detecte, %d → archive",
                res["to_plus_detecte"], res["to_archive"],
            )

    def job_weather_obs() -> None:
        # Spec 02 §2 : météo constatée pour chaque feu actif qualifié végétation,
        # au centroïde de l'empreinte ; déclenche le recalcul direction_vent (§7).
        fires = conn.execute(
            "SELECT id FROM fire_event WHERE lifecycle='actif' "
            "AND qualification='vegetation_confirme'"
        ).fetchall()
        n_ok = n_rel = 0
        for f in fires:
            footprint = fire_footprint_l93(conn, config, f["id"])
            if footprint is None:
                continue
            pt = geo.to_wgs84_geom(footprint.centroid)
            res = fetch_weather_obs(conn, config, fire_event_id=f["id"], lat=pt.y, lon=pt.x)
            if res["status"] != "ok":
                log.warning("weather_obs feu %s: %s", f["id"], res.get("error"))
                continue
            n_ok += 1
            obs = conn.execute(
                "SELECT observed_at FROM weather_obs WHERE id=?", (res["weather_obs_id"],)
            ).fetchone()
            rel = recompute_direction_vent(conn, config, f["id"], stamp=obs["observed_at"])
            n_rel += rel["opened"] + rel["closed"]
            # §8 : nouvelle weather_obs ⇒ fiche feu + fiches communes direction_vent
            # (pas la carte : un changement de vent n'est pas une nouvelle version).
            enqueue_fire_update(conn, f["id"], rel["communes"],
                                stamp=obs["observed_at"], trigger="weather_obs", carte=False)
        if fires:
            log.info(
                "weather_obs: %d/%d feux échantillonnés, %d relations direction_vent modifiées",
                n_ok, len(fires), n_rel,
            )
        ping_healthcheck(os.environ.get("HEALTHCHECK_WEATHER_URL"))

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

    def job_commune_context() -> None:
        # Spec 02 §2 : drought/vigieau quotidiens, par commune concernée par un feu
        # actif. Activation live derrière flag (config) tant que les formats d'API
        # ne sont pas vérifiés (cadrage Lot 3) : flag off ⇒ marche à blanc.
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        res = refresh_commune_context(conn, config, valid_date=today)
        log.info(
            "contexte communal: %d communes, %d depts (drought=%s, vigieau=%s) — "
            "vigieau %d, effis %d, meteo_forets %d",
            res["communes"], res["depts"], res["drought_activated"], res["vigieau_activated"],
            res["vigieau_inserted"], res["effis_inserted"], res["meteo_forets_inserted"],
        )
        ping_healthcheck(os.environ.get("HEALTHCHECK_CONTEXT_URL"))

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
        job_weather_obs, "interval",
        minutes=config["firms"]["fetch_interval_min"],
        id="weather_obs", max_instances=1, coalesce=True,
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
        job_lifecycle, "interval", hours=1,
        id="lifecycle", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_commune_context, "cron", hour=6, minute=0,   # matin (après publication EFFIS/MF)
        id="commune_context", max_instances=1, coalesce=True,
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
        "démarrage — fetch+moteur immédiat puis toutes les %d min ; "
        "backfill+gap+cycle de vie horaires ; archive 03h30",
        config["firms"]["fetch_interval_min"],
    )
    job_fetch_firms()  # premier cycle sans attendre l'intervalle
    scheduler.start()


if __name__ == "__main__":
    main()
