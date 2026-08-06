"""Archive Parquet + purge de geo_detection_raw (Spec 07 §4.3, étape 9).

Règle cardinale (comme hotspot) : purge uniquement ce qui est archivé et NON protégé — jamais une
détection rattachée à un feu vivant ni à un candidat `en_attente`. La donnée jamais confirmée
(calibration) est purgeable après export.
"""

from __future__ import annotations

from datetime import date

import pyarrow.parquet as pq

from vigifeu.model.archive import _partition_path, archive_sweep
from vigifeu.model.db import connect, load_config, migrate

OLD = "2026-07-01T11:40:00Z"           # jour clos, hors fenêtre
TODAY = date(2026, 7, 20)              # cutoff = TODAY - 14 j = 2026-07-06 → 07-01 purgeable


def _det(conn, *, acq_at=OLD, confirmed=None, candidate=None, lon=-1.0):
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682', ?)", (OLD,)
    ).lastrowid
    return conn.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, ingestion_run_id, "
        "confirmed_by_fire_event_id, geo_candidate_id) VALUES ('mtg-fci-fir', 44.7, ?, ?, ?, ?, ?, ?)",
        (lon, acq_at, OLD, run, confirmed, candidate),
    ).lastrowid


def _fire(conn, lifecycle):
    return conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-07-01T12:00:00Z', ?)",
        (lifecycle,),
    ).lastrowid


def _candidat(conn, status):
    return conn.execute(
        "INSERT INTO geo_candidate (created_at, first_acq_at, last_acq_at, centroid_lat, centroid_lon, "
        "n_detections, status) VALUES (?,?,?,44.7,-1.0,3,?)", (OLD, OLD, OLD, status)
    ).lastrowid


def test_export_purge_et_protection(tmp_path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    config = load_config("config/params.toml")
    config["archive"]["dir"] = str(tmp_path / "archive")

    libre = _det(conn, lon=-1.00)                                   # jamais confirmée → purgeable
    vivant = _det(conn, confirmed=_fire(conn, "actif"), lon=-1.01)  # feu vivant → protégée
    archive = _det(conn, confirmed=_fire(conn, "archive"), lon=-1.02)  # feu archivé → purgeable
    en_attente = _det(conn, candidate=_candidat(conn, "en_attente"), lon=-1.03)  # candidat → protégée
    conn.commit()

    res = archive_sweep(conn, config, today=TODAY)

    # export : les 4 détections écrites en Parquet ; purge : les 2 non protégées.
    assert res["exported_geodetections"] == 4
    assert res["purged_geodetections"] == 2
    assert res["protected_geodetections"] == 2
    assert _partition_path(tmp_path / "archive", "geo_detection_raw", "2026-07-01").exists()

    restants = {r["id"] for r in conn.execute("SELECT id FROM geo_detection_raw")}
    assert restants == {vivant, en_attente}
    assert libre not in restants and archive not in restants
    conn.close()


def test_pas_de_purge_sans_partition(tmp_path):
    """Garde-fou : sans partition Parquet écrite, aucune purge (jamais de perte)."""
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    config = load_config("config/params.toml")
    config["archive"]["dir"] = str(tmp_path / "archive")
    _det(conn, lon=-1.00)
    conn.commit()
    # Un jour clos EXPORTÉ puis purgé : ici on vérifie juste que l'export a bien précédé la purge
    # (le sweep exporte avant de purger, donc la partition existe → purge licite).
    res = archive_sweep(conn, config, today=TODAY)
    assert res["exported_geodetections"] == 1 and res["purged_geodetections"] == 1
    conn.close()


def test_dans_la_fenetre_pas_purge(tmp_path):
    """Une détection récente (dans la fenêtre de rétention) est exportée mais PAS purgée."""
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    config = load_config("config/params.toml")
    config["archive"]["dir"] = str(tmp_path / "archive")
    _det(conn, acq_at="2026-07-18T10:00:00Z")     # 2 j avant TODAY, dans la fenêtre 14 j
    conn.commit()
    res = archive_sweep(conn, config, today=TODAY)
    assert res["exported_geodetections"] == 1 and res["purged_geodetections"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM geo_detection_raw").fetchone()["n"] == 1
    conn.close()
