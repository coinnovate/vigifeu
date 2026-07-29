"""Garde-fou du rejeu : reset_interpretation doit effacer l'interprétation même quand
les tables qui référencent fire_event sont peuplées (weather_obs NOT NULL, auto-référence
merged_into, hotspot_raw.fire_event_id).

Bug prod (Lot 5) : le reset supprimait fire_event alors que weather_obs (peuplé dès que
le daemon échantillonne la météo — ce qu'il ne faisait plus avant le fix threading) et
merged_into le référençaient encore → `FOREIGN KEY constraint failed`. Les tests de rejeu
existants ne le voyaient pas : leur weather_obs était vide.
"""

from __future__ import annotations

from vigifeu.engine.pipeline import reset_interpretation
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources


def test_reset_avec_meteo_et_fusion_et_hotspot_rattache(tmp_path):
    conn = connect(tmp_path / "r.db")
    migrate(conn)
    config = load_config("config/params.toml")
    sync_satellite_sources(conn, config)

    src = conn.execute("SELECT id FROM satellite_source LIMIT 1").fetchone()["id"]
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('t', ?, 'ok')",
        ("2026-07-26T00:00:00Z",),
    ).lastrowid

    fe1 = conn.execute(
        "INSERT INTO fire_event (created_at, first_acq_at, last_acq_at, lifecycle, qualification) "
        "VALUES ('2026-07-26T00:05:00Z','2026-07-26T00:00:00Z','2026-07-26T12:00:00Z','actif','vegetation_confirme')"
    ).lastrowid
    # feu absorbé → merged_into (auto-référence vers fe1)
    conn.execute(
        "INSERT INTO fire_event (created_at, first_acq_at, lifecycle, qualification, merged_into) "
        "VALUES ('2026-07-26T00:05:00Z','2026-07-26T00:00:00Z','fusionne','vegetation_confirme', ?)",
        (fe1,),
    )
    # hotspot rattaché (observation conservée) + météo échantillonnée (dérivée, NOT NULL)
    conn.execute(
        "INSERT INTO hotspot_raw (source_id, lat, lon, acq_at, ingested_at, ingestion_run_id, fire_event_id) "
        "VALUES (?, 44.9, -1.0, '2026-07-26T00:00:00Z', '2026-07-26T00:05:00Z', ?, ?)",
        (src, run, fe1),
    )
    conn.execute(
        "INSERT INTO weather_obs (fire_event_id, observed_at, fetched_at, provider) "
        "VALUES (?, '2026-07-26T01:00:00Z', '2026-07-26T01:05:00Z', 'test')",
        (fe1,),
    )
    conn.commit()

    reset_interpretation(conn, config)  # ne doit PAS lever FOREIGN KEY constraint failed

    assert conn.execute("SELECT COUNT(*) FROM fire_event").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM weather_obs").fetchone()[0] == 0
    # l'observation est conservée, seul son lien d'interprétation est coupé
    assert conn.execute("SELECT COUNT(*) FROM hotspot_raw").fetchone()[0] == 1
    assert conn.execute("SELECT fire_event_id FROM hotspot_raw").fetchone()[0] is None
    # aucune référence orpheline laissée par le reset
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
