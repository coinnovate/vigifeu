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
    limit: int | None = None,
) -> int:
    """Charge la fixture Saumos dans hotspot_raw. Retourne le nombre de lignes insérées.

    - `day_prefix` : ne garder que les acq_at commençant par ce préfixe (ex. '2026-07-22') ;
    - `sources`    : restreindre à certains codes satellitaires ;
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
