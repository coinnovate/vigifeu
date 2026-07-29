"""Fetchers sécheresse/danger : EFFIS (FWI + sous-indices) et Météo des forêts.

Spec 01 §3.5 — table générique drought_obs, multi-indices, maille variable.
La traduction métier n'est PAS stockée (barèmes versionnés dans le code, améliorables
sans réécrire l'historique) ; seule `value_class` officielle l'est.

Formats :

- EFFIS (**hypothèse, non vérifiée**) : `GET {effis_url}?lat=&lon=&date=` renverrait les
  indices FWI canadiens au point de grille : fwi, ffmc, dmc, dc, isi, bui (REAL). Une
  ligne drought_obs par indice, maille (lat, lon). Source en réalité = WMS EFFIS, à
  recâbler ; laissée `effis_activated=false` en attendant.

- Météo des forêts (**vérifié live 29/07/2026**) : API DonnéesPubliques DPMeteoForets v1,
  `GET .../carte/departement/encours?format=json&echeance=J1&id-departement=NN`, en-tête
  `apikey`. Réponse = liste `[{reference_time, dep_code, dep_nom, niveau_j1[, niveau_j2]}]`,
  niveau "1".."4". Prévision J+1/J+2 (pas de J0). Une ligne indicator='meteo_forets', maille
  département.

Seules `_parse_effis` et `_parse_meteo_forets` dépendent du format. Une source en panne dégrade sans bloquer
(Spec 02 §9). Observations immuables : anti-doublon sur (indicator, maille, valid_date).
"""

from __future__ import annotations

import os
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


def _fetch_json(url: str, params: dict, config: dict, headers: dict | None = None) -> object:
    d = config["drought"]

    @retry(
        stop=stop_after_attempt(d["max_retries"]),
        wait=wait_exponential(min=d["retry_wait_min_s"], max=d["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, DroughtError)),
        reraise=True,
    )
    def _do() -> object:
        resp = httpx.get(url, params=params, headers=headers or {}, timeout=d["timeout_s"])
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


def _parse_meteo_forets(payload: object, echeance: str = "J1") -> dict | None:
    """Danger départemental Météo des forêts. SEUL point dépendant du format.

    Réponse réelle (DPMeteoForets v1, vérifiée live) : une **liste** d'objets par
    département `{reference_time, dep_code, dep_nom, niveau_j1[, niveau_j2]}`, le niveau
    étant une chaîne "1".."4" (1 faible/vert → 4 très élevé/rouge). Filtrée côté serveur
    par `id-departement`, la liste contient l'unique département demandé.
    """
    if not isinstance(payload, list) or not payload:
        return None
    entry = payload[0]
    champ = "niveau_j2" if echeance.upper() == "J2" else "niveau_j1"
    niveau = entry.get(champ)
    if niveau is None or niveau == "":
        return None
    try:
        val = float(niveau)
    except (TypeError, ValueError):
        val = None
    return {"value_class": str(niveau), "value": val}


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
    """Récupère le danger départemental Météo des forêts (DPMeteoForets v1). Ne lève jamais.

    Produit = prévision **J+1 / J+2** de la diffusion en cours (pas de date arbitraire ;
    l'historique est sur meteo.data.gouv.fr). Clé API en en-tête `apikey`, lue depuis
    l'environnement (jamais en config). Débit `Unlimited` côté API.
    """
    d = config["drought"]
    token = os.environ.get("VIGIFEU_METEOFRANCE_KEY")
    if not token:
        return {"status": "error", "error": "VIGIFEU_METEOFRANCE_KEY absente de l'environnement"}
    echeance = d.get("meteo_forets_echeance", "J1")
    params = {"format": "json", "echeance": echeance, "id-departement": dept}
    try:
        payload = _fetch_json(d["meteo_forets_url"], params, config, headers={"apikey": token})
        parsed = _parse_meteo_forets(payload, echeance)
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
        return {"status": "ok", "inserted": 1, "value_class": parsed["value_class"], "echeance": echeance}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
