"""Monitoring : détection des trous de collecte et pings healthchecks.io.

Spec 02 §9, plan §1.1. Le silence d'une source est une information critique en
saison. Deux mécanismes complémentaires :

- **Dead-man switch** : chaque tâche planifiée pingue une URL healthchecks.io à
  chaque succès. Un ping manquant (tâche morte, daemon arrêté) devient une alerte
  externe sans code de notre côté.
- **Trou de collecte** : si aucune ingestion FIRMS n'a réussi depuis plus de
  `gap_alert_hours`, on lève une alerte interne (log + ping /fail).

Le monitoring ne doit JAMAIS casser le daemon : un ping en échec est journalisé,
jamais propagé.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

import httpx

log = logging.getLogger("vigifeu")

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _now_iso() -> str:
    return datetime.now(UTC).strftime(_ISO)


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO).replace(tzinfo=UTC)


def ping_healthcheck(url: str | None, *, ok: bool = True) -> bool:
    """Pingue healthchecks.io (succès, ou /fail si ok=False). Ne lève jamais.

    Retourne True si le ping est parti, False si pas d'URL ou échec réseau.
    """
    if not url:
        return False
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        httpx.get(target, timeout=10)
        return True
    except httpx.HTTPError:
        log.warning("ping healthcheck échoué (%s)", target)
        return False


def last_successful_collection_at(conn: sqlite3.Connection) -> str | None:
    """Horodatage de la dernière ingestion FIRMS réussie (finished_at max)."""
    row = conn.execute(
        "SELECT MAX(finished_at) AS m FROM ingestion_run "
        "WHERE source LIKE 'firms:%' AND status='ok' AND finished_at IS NOT NULL"
    ).fetchone()
    return row["m"]


def check_collection_gap(
    conn: sqlite3.Connection, config: dict, now_iso: str | None = None
) -> dict:
    """Évalue le trou de collecte FIRMS. Retourne {alert, gap_hours, last_at, message}."""
    now_iso = now_iso or _now_iso()
    threshold = config["monitoring"]["gap_alert_hours"]
    last = last_successful_collection_at(conn)

    if last is None:
        return {
            "alert": True,
            "gap_hours": None,
            "last_at": None,
            "threshold_hours": threshold,
            "message": "aucune ingestion FIRMS réussie à ce jour",
        }

    gap_hours = (_parse(now_iso) - _parse(last)).total_seconds() / 3600
    alert = gap_hours > threshold
    return {
        "alert": alert,
        "gap_hours": round(gap_hours, 2),
        "last_at": last,
        "threshold_hours": threshold,
        "message": (
            f"trou de collecte FIRMS : {gap_hours:.1f} h sans succès "
            f"(seuil {threshold} h), dernière réussite {last}"
            if alert
            else None
        ),
    }
