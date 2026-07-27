"""Fetchers météo Open-Meteo : weather_obs (constatée) et weather_forecast (prévue).

Spec 01 §3.3/§3.4, catégories `mesuree`/`estimee` et `prevue` (P4).

Deux horodatages sur chaque ligne (P3) :
- observation : `observed_at` (validité de la mesure) / `fetched_at` (quand récupérée) ;
- prévision   : `valid_at` (échéance) + `model_run_at` (run) / `fetched_at`.

Unités Open-Meteo alignées sur la base (Spec 01 §7) : km/h, °C, mm, degrés.
Les réponses sont demandées en UTC (timezone=GMT, P7).

Ces fetchers écrivent une ligne par appel/échéance ; l'orchestration (échantillonner
tant qu'un feu est actif — Spec 02 §2) est portée par le scheduler. En Lot 1 ils
sont pilotables à la main ou par test, un feu réel n'existant qu'au Lot 2.

Une source météo en panne dégrade la page sans jamais la bloquer (Spec 02 §9) :
l'échec est journalisé/remonté, pas propagé.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class WeatherError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_utc(t: str) -> str:
    """Normalise un horodatage Open-Meteo (UTC, sans secondes) en ISO Z.

    Open-Meteo (timezone=GMT) renvoie ex. '2026-07-22T12:30' → '2026-07-22T12:30:00Z'.
    """
    if t.endswith("Z"):
        return t
    if len(t) == 16:  # YYYY-MM-DDTHH:MM
        return t + ":00Z"
    if len(t) == 19:  # avec secondes
        return t + "Z"
    return t


def _fetch_json(url: str, params: dict, config: dict) -> dict:
    w = config["weather"]

    @retry(
        stop=stop_after_attempt(w["max_retries"]),
        wait=wait_exponential(min=w["retry_wait_min_s"], max=w["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, WeatherError)),
        reraise=True,
    )
    def _do() -> dict:
        resp = httpx.get(url, params=params, timeout=w["timeout_s"])
        if resp.status_code in (429, 500, 502, 503, 504):
            raise WeatherError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code != 200:
            raise WeatherError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    return _do()


def fetch_weather_obs(
    conn: sqlite3.Connection,
    config: dict,
    *,
    fire_event_id: int,
    lat: float,
    lon: float,
) -> dict:
    """Échantillonne la météo courante au point (lat, lon) et l'insère (weather_obs).

    Retourne {status, weather_obs_id} ou {status:'error', error}. Ne lève jamais
    (Spec 02 §9 : une source météo en panne ne bloque pas le cycle).
    """
    w = config["weather"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": w["timezone"],
        "wind_speed_unit": "kmh",
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "wind_speed_10m",
                "wind_gusts_10m",
                "wind_direction_10m",
            ]
        ),
    }
    try:
        data = _fetch_json(w["base_url"], params, config)
        cur = data.get("current")
        if not cur:
            raise WeatherError("réponse sans bloc 'current'")
        wid = conn.execute(
            """INSERT INTO weather_obs
               (fire_event_id, lat, lon, observed_at, fetched_at, provider,
                wind_speed_kmh, wind_gusts_kmh, wind_dir_deg, temp_c, rh_pct,
                precip_mm_1h, precip_mm_24h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fire_event_id,
                lat,
                lon,
                _iso_utc(cur["time"]),
                _now_iso(),
                w["provider"],
                cur.get("wind_speed_10m"),
                cur.get("wind_gusts_10m"),
                cur.get("wind_direction_10m"),
                cur.get("temperature_2m"),
                cur.get("relative_humidity_2m"),
                cur.get("precipitation"),  # Open-Meteo : cumul de l'heure précédente
                None,  # precip_mm_24h : maille horaire non fournie ici (v1)
            ),
        ).lastrowid
        conn.commit()
        return {"status": "ok", "weather_obs_id": wid}
    except Exception as exc:  # noqa: BLE001 — dégrade, ne bloque pas (Spec 02 §9)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def fetch_weather_forecast(
    conn: sqlite3.Connection,
    config: dict,
    *,
    lat: float,
    lon: float,
    fire_event_id: int | None = None,
    code_insee: str | None = None,
    model: str = "best_match",
) -> dict:
    """Récupère la prévision horaire et insère une ligne par échéance (weather_forecast).

    Cible un feu OU une commune (au moins l'un des deux — contrainte du schéma).
    Les prévisions successives d'une même échéance coexistent (une par run) ;
    faute d'exposition du run par l'API gratuite, `model_run_at` est approximé par
    l'heure de récupération (v1, Spec 01 §3.4 — raffinable sans changer le schéma).
    """
    if fire_event_id is None and code_insee is None:
        return {"status": "error", "error": "cible absente (feu ou commune requis)"}

    w = config["weather"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": w["timezone"],
        "wind_speed_unit": "kmh",
        "forecast_days": w["forecast_days"],
        "hourly": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "precipitation_probability",
                "wind_speed_10m",
                "wind_direction_10m",
            ]
        ),
    }
    try:
        data = _fetch_json(w["base_url"], params, config)
        hourly = data.get("hourly")
        if not hourly or not hourly.get("time"):
            raise WeatherError("réponse sans bloc 'hourly'")

        run_at = _now_iso()  # proxy du run (v1)
        fetched = run_at
        times = hourly["time"]
        rows = []
        for i, t in enumerate(times):
            rows.append(
                (
                    fire_event_id,
                    code_insee,
                    w["provider"],
                    model,
                    run_at,
                    _iso_utc(t),
                    _at(hourly, "precipitation", i),
                    _at(hourly, "precipitation_probability", i),
                    _at(hourly, "wind_speed_10m", i),
                    _at(hourly, "wind_direction_10m", i),
                    _at(hourly, "temperature_2m", i),
                    _at(hourly, "relative_humidity_2m", i),
                    fetched,
                )
            )
        conn.executemany(
            """INSERT INTO weather_forecast
               (fire_event_id, code_insee, provider, model, model_run_at, valid_at,
                precip_mm, precip_prob_pct, wind_speed_kmh, wind_dir_deg, temp_c, rh_pct,
                fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return {"status": "ok", "n_echeances": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _at(block: dict, key: str, i: int):
    """Valeur i d'une série horaire Open-Meteo, tolérante aux séries absentes."""
    series = block.get(key)
    if series is None or i >= len(series):
        return None
    return series[i]
