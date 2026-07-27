"""Tests des fetchers météo Open-Meteo (Spec 01 §3.3/§3.4).

Le HTTP est mocké avec des réponses au format Open-Meteo réel (timezone=GMT).
Vérifie le double horodatage (P3), les unités de base et la dégradation sans
blocage (Spec 02 §9).
"""

from __future__ import annotations

import pytest

from vigifeu.ingest import weather


def _make_fire(conn) -> int:
    return conn.execute(
        "INSERT INTO fire_event (created_at) VALUES ('2026-07-22T12:00:00Z')"
    ).lastrowid


CURRENT_JSON = {
    "latitude": 44.9,
    "longitude": -1.15,
    "current_units": {
        "time": "iso8601", "temperature_2m": "°C", "wind_speed_10m": "km/h",
    },
    "current": {
        "time": "2026-07-22T12:30",
        "temperature_2m": 31.2,
        "relative_humidity_2m": 28,
        "precipitation": 0.0,
        "wind_speed_10m": 24.5,
        "wind_gusts_10m": 41.0,
        "wind_direction_10m": 315,
    },
}

FORECAST_JSON = {
    "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "hourly": {
        "time": ["2026-07-22T13:00", "2026-07-22T14:00", "2026-07-22T15:00"],
        "temperature_2m": [31.5, 32.0, 31.0],
        "relative_humidity_2m": [27, 25, 26],
        "precipitation": [0.0, 0.0, 0.2],
        "precipitation_probability": [0, 5, 20],
        "wind_speed_10m": [25.0, 27.5, 26.0],
        "wind_direction_10m": [310, 320, 315],
    },
}


def test_weather_obs_insertion(db, monkeypatch):
    conn, config = db
    fid = _make_fire(conn)
    monkeypatch.setattr(weather, "_fetch_json", lambda *a, **k: CURRENT_JSON)

    r = weather.fetch_weather_obs(conn, config, fire_event_id=fid, lat=44.9, lon=-1.15)
    assert r["status"] == "ok"

    row = conn.execute("SELECT * FROM weather_obs").fetchone()
    assert row["fire_event_id"] == fid
    assert row["observed_at"] == "2026-07-22T12:30:00Z"  # normalisé en ISO Z (P7)
    assert row["fetched_at"].endswith("Z")
    assert row["observed_at"] != row["fetched_at"]        # double horodatage (P3)
    assert row["provider"] == "open-meteo"
    assert row["wind_speed_kmh"] == 24.5                  # unité de base km/h
    assert row["wind_gusts_kmh"] == 41.0
    assert row["wind_dir_deg"] == 315
    assert row["temp_c"] == 31.2
    assert row["rh_pct"] == 28
    assert row["precip_mm_1h"] == 0.0


def test_weather_forecast_une_ligne_par_echeance(db, monkeypatch):
    conn, config = db
    fid = _make_fire(conn)
    monkeypatch.setattr(weather, "_fetch_json", lambda *a, **k: FORECAST_JSON)

    r = weather.fetch_weather_forecast(conn, config, fire_event_id=fid, lat=44.9, lon=-1.15)
    assert r["status"] == "ok"
    assert r["n_echeances"] == 3

    rows = conn.execute("SELECT * FROM weather_forecast ORDER BY valid_at").fetchall()
    assert [x["valid_at"] for x in rows] == [
        "2026-07-22T13:00:00Z", "2026-07-22T14:00:00Z", "2026-07-22T15:00:00Z",
    ]
    assert rows[2]["precip_mm"] == 0.2
    assert rows[2]["precip_prob_pct"] == 20
    assert rows[0]["wind_speed_kmh"] == 25.0
    # Identifiée par son run (proxy = fetched en v1) ; toutes les échéances d'un
    # même appel partagent le run.
    assert len({x["model_run_at"] for x in rows}) == 1


def test_forecast_cible_commune(db, monkeypatch):
    conn, config = db
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33333','le-porge','Le Porge')")
    conn.commit()
    monkeypatch.setattr(weather, "_fetch_json", lambda *a, **k: FORECAST_JSON)

    r = weather.fetch_weather_forecast(conn, config, code_insee="33333", lat=44.9, lon=-1.15)
    assert r["status"] == "ok"
    row = conn.execute("SELECT * FROM weather_forecast LIMIT 1").fetchone()
    assert row["code_insee"] == "33333"
    assert row["fire_event_id"] is None


def test_forecast_sans_cible_refuse(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(weather, "_fetch_json", lambda *a, **k: FORECAST_JSON)
    r = weather.fetch_weather_forecast(conn, config, lat=44.9, lon=-1.15)
    assert r["status"] == "error"
    assert conn.execute("SELECT COUNT(*) AS n FROM weather_forecast").fetchone()["n"] == 0


def test_panne_meteo_ne_bloque_pas(db, monkeypatch):
    """Spec 02 §9 : une source météo en panne dégrade sans lever ni bloquer."""
    conn, config = db
    fid = _make_fire(conn)

    def boom(*a, **k):
        raise weather.WeatherError("HTTP 503 (réessayable)")

    monkeypatch.setattr(weather, "_fetch_json", boom)
    r = weather.fetch_weather_obs(conn, config, fire_event_id=fid, lat=44.9, lon=-1.15)
    assert r["status"] == "error"
    assert "503" in r["error"]
    assert conn.execute("SELECT COUNT(*) AS n FROM weather_obs").fetchone()["n"] == 0


def test_iso_utc_normalisation():
    assert weather._iso_utc("2026-07-22T12:30") == "2026-07-22T12:30:00Z"
    assert weather._iso_utc("2026-07-22T12:30:00Z") == "2026-07-22T12:30:00Z"
    assert weather._iso_utc("2026-07-22T12:30:45") == "2026-07-22T12:30:45Z"
