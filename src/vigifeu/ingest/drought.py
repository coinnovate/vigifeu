"""Fetchers sécheresse/danger : EFFIS (FWI + sous-indices) et Météo des forêts.

Spec 01 §3.5 — table générique drought_obs, multi-indices, maille variable.
La traduction métier n'est PAS stockée (barèmes versionnés dans le code, améliorables
sans réécrire l'historique) ; seule `value_class` officielle l'est.

HYPOTHÈSES DE FORMAT (à vérifier contre les API réelles avant production) :

- EFFIS : `GET {effis_url}?lat=&lon=&date=` renvoie un objet portant les indices du
  système FWI canadien au point de grille : fwi, ffmc, dmc, dc, isi, bui (REAL).
  On insère une ligne drought_obs par indice présent, maille (lat, lon).

- Météo des forêts : `GET {meteo_forets_url}?dept=&date=` renvoie le niveau de danger
  départemental officiel (value_class : vert/jaune/orange/rouge), éventuellement une
  valeur numérique. On insère une ligne indicator='meteo_forets', maille département.

Seules `_parse_effis` et `_parse_meteo_forets` dépendent du format : une passe de
vérification live ne touchera qu'elles. Une source en panne dégrade sans bloquer
(Spec 02 §9). Observations immuables : anti-doublon sur (indicator, maille, valid_date).
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

# Sous-indices du système FWI canadien exposés par EFFIS.
_EFFIS_INDICATEURS = ("fwi", "ffmc", "dmc", "dc", "isi", "bui")


class DroughtError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_json(url: str, params: dict, config: dict) -> dict:
    d = config["drought"]

    @retry(
        stop=stop_after_attempt(d["max_retries"]),
        wait=wait_exponential(min=d["retry_wait_min_s"], max=d["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, DroughtError)),
        reraise=True,
    )
    def _do() -> dict:
        resp = httpx.get(url, params=params, timeout=d["timeout_s"])
        if resp.status_code in (429, 500, 502, 503, 504):
            raise DroughtError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code != 200:
            raise DroughtError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    return _do()


def _parse_effis(payload: dict) -> dict[str, float]:
    """Indices FWI présents dans une réponse EFFIS. SEUL point dépendant du format."""
    out: dict[str, float] = {}
    for ind in _EFFIS_INDICATEURS:
        val = payload.get(ind)
        if val is not None:
            out[ind] = float(val)
    return out


def _parse_meteo_forets(payload: dict) -> dict | None:
    """(value_class, value) du danger départemental. SEUL point dépendant du format."""
    classe = payload.get("value_class") or payload.get("niveau") or payload.get("couleur")
    if classe is None:
        return None
    val = payload.get("value")
    return {"value_class": classe, "value": float(val) if val is not None else None}


def _deja_present(
    conn: sqlite3.Connection, indicator: str, valid_date: str, *,
    code_insee: str | None = None, dept: str | None = None,
    lat: float | None = None, lon: float | None = None,
) -> bool:
    row = conn.execute(
        """SELECT 1 FROM drought_obs
           WHERE indicator=? AND valid_date=?
             AND IFNULL(code_insee,'')=IFNULL(?,'')
             AND IFNULL(dept,'')=IFNULL(?,'')
             AND IFNULL(lat,-999)=IFNULL(?,-999)
             AND IFNULL(lon,-999)=IFNULL(?,-999)
           LIMIT 1""",
        (indicator, valid_date, code_insee, dept, lat, lon),
    ).fetchone()
    return row is not None


def fetch_effis_fwi(
    conn: sqlite3.Connection,
    config: dict,
    *,
    lat: float,
    lon: float,
    valid_date: str,
    code_insee: str | None = None,
) -> dict:
    """Récupère FWI + sous-indices EFFIS au point (lat, lon) pour un jour. Ne lève jamais."""
    d = config["drought"]
    params = {"lat": lat, "lon": lon, "date": valid_date}
    try:
        payload = _fetch_json(d["effis_url"], params, config)
        indices = _parse_effis(payload)
        if not indices:
            return {"status": "ok", "inserted": 0, "reason": "aucun indice"}
        fetched = _now_iso()
        n = 0
        for indicator, value in indices.items():
            if _deja_present(conn, indicator, valid_date, lat=lat, lon=lon):
                continue
            conn.execute(
                """INSERT INTO drought_obs
                   (indicator, code_insee, lat, lon, valid_date, value, provider, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (indicator, code_insee, lat, lon, valid_date, value, d["provider_effis"], fetched),
            )
            n += 1
        conn.commit()
        return {"status": "ok", "inserted": n, "indicateurs": list(indices)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def fetch_meteo_forets(
    conn: sqlite3.Connection, config: dict, *, dept: str, valid_date: str
) -> dict:
    """Récupère le danger départemental Météo des forêts (value_class). Ne lève jamais."""
    d = config["drought"]
    params = {"dept": dept, "date": valid_date}
    try:
        payload = _fetch_json(d["meteo_forets_url"], params, config)
        parsed = _parse_meteo_forets(payload)
        if parsed is None:
            return {"status": "ok", "inserted": 0, "reason": "aucun niveau"}
        if _deja_present(conn, "meteo_forets", valid_date, dept=dept):
            return {"status": "ok", "inserted": 0, "reason": "inchangé"}
        conn.execute(
            """INSERT INTO drought_obs
               (indicator, dept, valid_date, value, value_class, provider, fetched_at)
               VALUES ('meteo_forets', ?, ?, ?, ?, ?, ?)""",
            (dept, valid_date, parsed["value"], parsed["value_class"],
             d["provider_meteo_forets"], _now_iso()),
        )
        conn.commit()
        return {"status": "ok", "inserted": 1, "value_class": parsed["value_class"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
