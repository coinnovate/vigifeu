"""Tests de l'archivage Parquet et de la purge (Spec 01 §6, Spec 02 §2/§10).

Vérifie la règle cardinale : la purge ne retire que ce qui est déjà archivé, et
jamais un hotspot rattaché à un feu non archivé.
"""

from __future__ import annotations

from datetime import date

import pyarrow.parquet as pq

from vigifeu.model.archive import (
    _partition_path,
    archive_sweep,
    export_hotspots_day,
)

from .conftest import load_saumos_hotspots


def _lier_a_un_feu(conn, hotspot_id: int, lifecycle: str = "actif") -> None:
    """Rattache un hotspot à un feu (via une version) avec le cycle de vie donné."""
    fe = conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-07-22T12:00:00Z', ?)",
        (lifecycle,),
    ).lastrowid
    fev = conn.execute(
        "INSERT INTO fire_event_version (fire_event_id, version_n, computed_at) "
        "VALUES (?, 1, '2026-07-22T12:35:00Z')",
        (fe,),
    ).lastrowid
    conn.execute(
        "INSERT INTO fe_hotspot (fire_event_version_id, hotspot_id) VALUES (?, ?)",
        (fev, hotspot_id),
    )
    conn.commit()


def test_export_partition_relisible(db, tmp_path):
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22")

    path, n = export_hotspots_day(conn, config, "2026-07-22")
    assert n > 0
    assert path.exists()
    assert path == _partition_path(tmp_path / "archive", "hotspot_raw", "2026-07-22")

    t = pq.read_table(path)
    assert t.num_rows == n
    assert "acq_at" in t.column_names and "raw_payload" in t.column_names


def test_purge_hors_fenetre_apres_archive(db, tmp_path):
    """Jours anciens : exportés puis purgés de SQLite (fenêtre glissante)."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    total = conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]

    # today très postérieur → tout est hors des 14 jours.
    res = archive_sweep(conn, config, today=date(2026, 8, 15))

    assert res["exported_hotspots"] == total
    assert res["purged_hotspots"] == total
    assert conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"] == 0
    # La donnée persiste dans l'archive (P1 : rien de perdu).
    path = _partition_path(tmp_path / "archive", "hotspot_raw", "2026-07-22")
    assert pq.read_table(path).num_rows == total


def test_fenetre_vivante_conservee(db, tmp_path):
    """Jours récents (dans la fenêtre) : exportés mais conservés en SQLite."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    total = conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]

    # today = 30/07, rétention 14 j → cutoff 16/07 : le 22/07 est encore vivant.
    res = archive_sweep(conn, config, today=date(2026, 7, 30))
    assert res["purged_hotspots"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"] == total
    # Mais déjà exporté (archive tenue à jour).
    assert res["exported_hotspots"] == total


def test_hotspot_de_feu_vivant_jamais_purge(db, tmp_path):
    """Règle cardinale : un hotspot ancien rattaché à un feu ACTIF n'est pas purgé."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22", sources={"VIIRS_SNPP_NRT"})
    protege = conn.execute("SELECT id FROM hotspot_raw ORDER BY id LIMIT 1").fetchone()["id"]
    _lier_a_un_feu(conn, protege, lifecycle="actif")

    res = archive_sweep(conn, config, today=date(2026, 8, 15))

    assert res["protected_hotspots"] == 1
    reste = conn.execute("SELECT id FROM hotspot_raw").fetchall()
    assert [r["id"] for r in reste] == [protege]  # seul le protégé survit


def test_hotspot_de_feu_archive_est_purgeable(db, tmp_path):
    """Un feu déjà archivé ne protège plus ses hotspots (Spec 01 §6)."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22", sources={"VIIRS_SNPP_NRT"})
    hid = conn.execute("SELECT id FROM hotspot_raw ORDER BY id LIMIT 1").fetchone()["id"]
    _lier_a_un_feu(conn, hid, lifecycle="archive")
    # On retire le lien fe_hotspot (le feu archivé a été exporté hors SQLite) pour
    # éviter la contrainte FK — cohérent avec le flux d'archivage d'un feu.
    conn.execute("DELETE FROM fe_hotspot")
    conn.commit()

    res = archive_sweep(conn, config, today=date(2026, 8, 15))
    assert res["protected_hotspots"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"] == 0


def test_purge_journal_ingestion(db, tmp_path):
    """ingestion_run au-delà de la rétention : archivé mensuellement puis purgé."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) "
        "VALUES ('firms:X', '2026-01-15T02:00:00Z', 'ok')"
    )
    conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) "
        "VALUES ('firms:X', '2026-08-14T02:00:00Z', 'ok')"
    )
    conn.commit()

    res = archive_sweep(conn, config, today=date(2026, 8, 15))
    assert res["purged_runs"] == 1  # seul janvier est hors des 90 jours
    restant = conn.execute("SELECT started_at FROM ingestion_run").fetchall()
    assert len(restant) == 1
    part = tmp_path / "archive" / "ingestion_run" / "annee=2026" / "mois=01" / "ingestion_run.parquet"
    assert pq.read_table(part).num_rows == 1


def test_sweep_idempotent(db, tmp_path):
    """Un second passage sans nouveauté ne purge rien de plus."""
    conn, config = db
    config["archive"]["dir"] = str(tmp_path / "archive")
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    archive_sweep(conn, config, today=date(2026, 8, 15))
    res2 = archive_sweep(conn, config, today=date(2026, 8, 15))
    assert res2["purged_hotspots"] == 0
    assert res2["exported_hotspots"] == 0  # plus aucun jour clos en base
