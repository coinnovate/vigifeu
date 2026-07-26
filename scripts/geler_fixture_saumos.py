"""Constitution de la fixture Saumos (Lot 0, jalon L0).

Ingère l'archive FIRMS du 20 au 27 juillet 2026 (France entière) dans une base
dédiée, puis gèle hotspot_raw en Parquet dans tests/fixtures/saumos/.

Usage :
    FIRMS_MAP_KEY=... uv run python scripts/geler_fixture_saumos.py

Notes :
- les ingested_at de cette fixture mesurent le backfill, pas le NRT — elle sert
  au rejeu du moteur (jalon L2), pas à la mesure de latence ;
- le fichier Parquet est commité dans le repo : c'est le jeu de référence
  contractuel de tous les tests de rejeu (Spec 02 §10.1).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from vigifeu.ingest.firms import ingest_day
from vigifeu.model.db import active_sources, connect, load_config, migrate, sync_satellite_sources

START, END = date(2026, 7, 20), date(2026, 7, 27)
OUT = Path("tests/fixtures/saumos/hotspots_2026-07-20_27_france.parquet")


def main() -> None:
    config = load_config()
    conn = connect("data/fixture_saumos.db")
    migrate(conn)
    sync_satellite_sources(conn, config)

    day = START
    while day <= END:
        for src in active_sources(conn):
            r = ingest_day(conn, config, src, day)
            print(f"{src['code']:>18} {day}: {r}")
            if r["status"] == "error":
                raise SystemExit(f"échec {src['code']} {day} — fixture incomplète, on s'arrête")
        day += timedelta(days=1)

    rows = conn.execute(
        """SELECT s.code AS source, h.lat, h.lon, h.acq_at, h.frp_mw,
                  h.confidence, h.scan_km, h.track_km, h.day_night, h.raw_payload
           FROM hotspot_raw h JOIN satellite_source s ON s.id = h.source_id
           ORDER BY h.acq_at, s.code"""
    ).fetchall()
    table = pa.Table.from_pylist([dict(r) for r in rows])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, OUT, compression="zstd")
    print(f"\nfixture gelée : {OUT} — {table.num_rows} hotspots")
    print("→ vérifier la présence du premier hotspot Saumos attendu (22/07 12:32 UTC),")
    print("  puis commiter le fichier.")


if __name__ == "__main__":
    main()
