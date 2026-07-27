"""Fixtures partagées et chargement de la fixture Saumos.

La fixture Saumos (tests/fixtures/saumos/, jamais modifiée) est l'archive FIRMS
réelle du 20-27 juillet 2026 sur la France. On la charge dans une base de test
pour rejouer le moteur sur des données authentiques.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources

FIXTURE_SAUMOS = (
    Path(__file__).parent / "fixtures" / "saumos" / "hotspots_2026-07-20_27_france.parquet"
)


@pytest.fixture()
def db(tmp_path):
    """Base migrée + config + sources satellitaires synchronisées."""
    c = connect(tmp_path / "test.db")
    migrate(c)
    config = load_config("config/params.toml")
    sync_satellite_sources(c, config)
    yield c, config
    c.close()


def load_saumos_hotspots(
    conn: sqlite3.Connection,
    *,
    day_prefix: str | None = None,
    sources: set[str] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int | None = None,
) -> int:
    """Charge la fixture Saumos dans hotspot_raw. Retourne le nombre de lignes insérées.

    - `day_prefix` : ne garder que les acq_at commençant par ce préfixe (ex. '2026-07-22') ;
    - `sources`    : restreindre à certains codes satellitaires ;
    - `bbox`       : (lat_min, lat_max, lon_min, lon_max) — restreindre spatialement
                     (ex. la Gironde ouest, pour un rejeu de clustering ciblé et rapide) ;
    - `limit`      : plafond (après filtres), pour des tests rapides.

    ingested_at est synthétisé (la fixture ne le porte pas) : il n'est pas
    l'objet des tests du moteur, qui portent sur acq_at.
    """
    import pyarrow.parquet as pq

    table = pq.read_table(FIXTURE_SAUMOS).to_pylist()

    code_to_id = {
        r["code"]: r["id"]
        for r in conn.execute("SELECT id, code FROM satellite_source")
    }

    run_id = conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('fixture:saumos', ?, 'ok')",
        (datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).lastrowid

    ingested = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for row in table:
        if day_prefix and not row["acq_at"].startswith(day_prefix):
            continue
        if sources and row["source"] not in sources:
            continue
        if bbox is not None:
            lat_min, lat_max, lon_min, lon_max = bbox
            if not (lat_min <= row["lat"] <= lat_max and lon_min <= row["lon"] <= lon_max):
                continue
        src_id = code_to_id.get(row["source"])
        if src_id is None:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO hotspot_raw
               (source_id, lat, lon, acq_at, ingested_at, ingestion_run_id,
                frp_mw, confidence, scan_km, track_km, day_night, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                src_id, row["lat"], row["lon"], row["acq_at"], ingested, run_id,
                row["frp_mw"], row["confidence"], row["scan_km"], row["track_km"],
                row["day_night"], row["raw_payload"],
            ),
        )
        n += 1
        if limit and n >= limit:
            break
    conn.commit()
    return n


def insert_hotspot(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    acq_at: str,
    *,
    source: str = "VIIRS_SNPP_NRT",
    frp: float = 10.0,
    day_night: str = "D",
    overpass_id: int = 1,
) -> int:
    """Insère un hotspot synthétique déjà rattaché à un passage (overpass_id posé).

    Pour les tests d'algorithme du moteur qui veulent des scénarios contrôlés sans
    passer par la fixture réelle. overpass_id est arbitraire mais non-NULL (le
    clustering ne traite que les hotspots rattachés à un passage).
    """
    src_id = conn.execute(
        "SELECT id FROM satellite_source WHERE code=?", (source,)
    ).fetchone()["id"]
    run_id = conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('synthetic', ?, 'ok')",
        (datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).lastrowid
    return conn.execute(
        """INSERT INTO hotspot_raw
           (source_id, lat, lon, acq_at, ingested_at, ingestion_run_id, frp_mw,
            day_night, overpass_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (src_id, lat, lon, acq_at, acq_at, run_id, frp, day_night, overpass_id),
    ).lastrowid
