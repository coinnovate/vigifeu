"""Garde-fou du daemon : les jobs planifiés doivent pouvoir ÉCRIRE en base.

Trou historique : aucun test n'exécutait un vrai job via APScheduler. Or
`BlockingScheduler` lance ses jobs dans un thread worker, pas le thread principal
qui a ouvert la connexion SQLite. Avec la garde `check_same_thread` par défaut,
chaque écriture planifiée levait `ProgrammingError` — le service restait « actif »
mais n'écrivait plus rien après le cycle de boot (appelé, lui, en direct).

Ces tests verrouillent le correctif : `connect(cross_thread=True)` + `make_scheduler`
(executor à un seul worker → jobs sérialisés = écrivain unique préservé, plan §1.1).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from vigifeu.model.db import connect
from vigifeu.scheduler import make_scheduler


def test_job_planifie_peut_ecrire(tmp_path):
    """Un job lancé par le scheduler (thread worker) écrit sans ProgrammingError."""
    conn = connect(tmp_path / "d.db", cross_thread=True)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()

    done = threading.Event()
    result: dict = {}

    scheduler = make_scheduler()

    def job() -> None:
        try:
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            result["n"] = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            result["worker_tid"] = threading.get_ident()
        except Exception as exc:  # noqa: BLE001 — on veut capturer l'échec cross-thread
            result["error"] = repr(exc)
        finally:
            done.set()
            scheduler.shutdown(wait=False)

    scheduler.add_job(job, "date", run_date=datetime.now(UTC) + timedelta(seconds=0.2))

    runner = threading.Thread(target=scheduler.start)
    runner.start()
    assert done.wait(timeout=10), "le job planifié ne s'est jamais exécuté"
    runner.join(timeout=5)

    assert "error" not in result, result.get("error")
    assert result["n"] == 1
    # Preuve que le job tournait bien HORS du thread principal (sinon le test ne
    # démontre rien) : le correctif est nécessaire, pas un no-op.
    assert result["worker_tid"] != threading.get_ident()

    conn.close()


def test_connect_par_defaut_reste_garde(tmp_path):
    """La CLI/les tests (mono-thread) gardent la garde stricte : usage cross-thread interdit."""
    conn = connect(tmp_path / "g.db")  # cross_thread=False par défaut
    captured: dict = {}

    def use() -> None:
        try:
            conn.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            captured["type"] = type(exc).__name__

    t = threading.Thread(target=use)
    t.start()
    t.join()
    assert captured.get("type") == "ProgrammingError"
    conn.close()


def test_make_scheduler_un_seul_worker():
    """L'executor par défaut est bien à un seul worker (sérialisation = écrivain unique)."""
    scheduler = make_scheduler()
    executor = scheduler._executors["default"]
    # apscheduler.executors.pool.ThreadPoolExecutor enveloppe concurrent.futures.
    assert executor._pool._max_workers == 1


def test_make_scheduler_misfire_genereux():
    """Un job en attente derrière le worker unique ne doit PAS être abandonné (« missed ») :
    misfire_grace_time généreux + coalesce, sinon weather_obs (même tick que fetch_firms)
    est systématiquement sauté."""
    scheduler = make_scheduler()
    assert scheduler._job_defaults["misfire_grace_time"] >= 600
    assert scheduler._job_defaults["coalesce"] is True


def test_chemin_generation_daemon_sur_connexion_cross_thread(tmp_path):
    """Le chemin exact que le daemon appelle (sync_static → consume → finalize_site)
    tourne sur une connexion cross_thread, comme en prod. Base vide : on vérifie qu'il
    n'explose pas et produit un site minimal complet (carte-config, robots, sitemap)."""
    from vigifeu.generate.runner import consume, finalize_site, sync_static
    from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources

    conn = connect(tmp_path / "d.db", cross_thread=True)
    migrate(conn)
    config = load_config("config/params.toml")
    config["generate"]["site_dir"] = str(tmp_path / "site")
    sync_satellite_sources(conn, config)

    sync_static(config)
    stats = consume(conn, config, stamp="2026-07-28T00:00:00Z")
    finalize_site(conn, config)

    out = tmp_path / "site"
    assert stats["erreurs"] == 0
    assert (out / "static" / "carte-config.js").exists()
    assert (out / "robots.txt").exists()
    assert list(out.glob("sitemap*.xml")), "aucun sitemap produit"
    conn.close()
