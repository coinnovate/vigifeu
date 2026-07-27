"""Fetcher VigiEau : restrictions d'eau par commune (Spec 01 §3.6, catégorie declaree).

HYPOTHÈSE DE FORMAT (à vérifier contre l'API réelle avant production) :
`GET {base_url}/zones?commune={code_insee}&profil={profil}` renvoie une liste de
zones, chacune portant :
  - `niveauGravite` : vigilance / alerte / alerte_renforcee / crise ;
  - `arrete` : { numero|numeroArrete|id, dateDebutValidite, dateFinValidite }.
Une commune peut relever de plusieurs zones (eaux superficielles/souterraines) :
on retient la restriction la plus sévère en vigueur.

Seule la fonction `_parse_zones` dépend du format : une passe de vérification live
ne touchera qu'elle. Tout le format observé est traité comme donnée, jamais comme
instruction.

Anti-doublon : une observation n'est insérée que si elle diffère de la dernière
connue pour la commune (même arrêté + même niveau = rien à réinsérer). Les
observations restent immuables (P1) — on n'écrase jamais, on n'ajoute que du neuf.
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

# Sévérité croissante — sert à retenir la zone la plus contraignante.
_SEVERITE = {"vigilance": 1, "alerte": 2, "alerte_renforcee": 3, "crise": 4}


class VigieauError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_json(url: str, params: dict, config: dict) -> object:
    v = config["vigieau"]

    @retry(
        stop=stop_after_attempt(v["max_retries"]),
        wait=wait_exponential(min=v["retry_wait_min_s"], max=v["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, VigieauError)),
        reraise=True,
    )
    def _do():
        resp = httpx.get(url, params=params, timeout=v["timeout_s"])
        if resp.status_code in (429, 500, 502, 503, 504):
            raise VigieauError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code == 404:
            return []  # commune sans zone connue = pas de restriction
        if resp.status_code != 200:
            raise VigieauError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    return _do()


def _parse_zones(payload: object) -> dict | None:
    """Extrait la restriction la plus sévère d'une réponse VigiEau.

    Retourne {niveau, date_debut, date_fin, arrete_ref} ou None si aucune
    restriction. SEULE fonction dépendante du format (point de vérification live).
    """
    # La réponse peut être une liste de zones, ou un objet englobant {zones:[...]}.
    zones = payload if isinstance(payload, list) else payload.get("zones", []) if isinstance(payload, dict) else []
    meilleure = None
    meilleure_sev = 0
    for z in zones:
        niveau = z.get("niveauGravite")
        sev = _SEVERITE.get(niveau, 0)
        if sev == 0:
            continue  # niveau inconnu ou "pas de restriction" → ignoré
        if sev > meilleure_sev:
            arrete = z.get("arrete") or {}
            meilleure = {
                "niveau": niveau,
                "date_debut": arrete.get("dateDebutValidite"),
                "date_fin": arrete.get("dateFinValidite"),
                "arrete_ref": (
                    arrete.get("numeroArrete")
                    or arrete.get("numero")
                    or (str(arrete["id"]) if arrete.get("id") is not None else None)
                ),
            }
            meilleure_sev = sev
    return meilleure


def fetch_vigieau(conn: sqlite3.Connection, config: dict, code_insee: str) -> dict:
    """Récupère et enregistre la restriction d'eau courante d'une commune.

    Ne lève jamais (Spec 02 §9) : une source en panne dégrade la fiche sans la
    bloquer. Retourne {status, inserted|reason|error}.
    """
    v = config["vigieau"]
    params = {"commune": code_insee, "profil": v["profil"]}
    try:
        payload = _fetch_json(f"{v['base_url']}/zones", params, config)
        restriction = _parse_zones(payload)
        if restriction is None:
            return {"status": "ok", "inserted": 0, "reason": "aucune restriction"}

        # Anti-doublon : identique à la dernière observation connue ?
        dernier = conn.execute(
            "SELECT niveau, arrete_ref FROM vigieau_arrete "
            "WHERE code_insee=? ORDER BY id DESC LIMIT 1",
            (code_insee,),
        ).fetchone()
        if (
            dernier is not None
            and dernier["niveau"] == restriction["niveau"]
            and dernier["arrete_ref"] == restriction["arrete_ref"]
        ):
            return {"status": "ok", "inserted": 0, "reason": "inchangé"}

        conn.execute(
            """INSERT INTO vigieau_arrete
               (code_insee, niveau, date_debut, date_fin, arrete_ref, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                code_insee,
                restriction["niveau"],
                restriction["date_debut"] or _now_iso()[:10],
                restriction["date_fin"],
                restriction["arrete_ref"],
                _now_iso(),
            ),
        )
        conn.commit()
        return {"status": "ok", "inserted": 1, "niveau": restriction["niveau"]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
