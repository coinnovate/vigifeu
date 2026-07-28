"""Alimentation de `regen_queue` — étape 9 du cycle (Spec 02 §8).

Le pipeline émet la liste minimale des pages impactées ; il n'écrit jamais de HTML
(le générateur du Lot 4 consomme la file). Au Lot 3 la file **s'accumule** sans
consommateur — c'est un test grandeur nature (plan §2, Lot 3).

Règles Spec 02 §8 câblées ici :
* **carte** nationale : si au moins un FireEvent publié a changé (nouvelle version) ;
* **feu** : FireEvent avec nouvelle version OU nouvelle weather_obs ;
* **commune** : commune dont une fe_commune_rel a été ouverte ou fermée (y compris
  direction_vent sur simple changement de vent).

Les fiches communes « rien à signaler » (régénérées 1×/jour après fetch_drought) et
la passe nocturne (sitemaps) ne relèvent PAS du cycle court — hors de ce module.

Dédup : on n'empile pas deux fois la même page tant qu'elle est en attente
(processed_at NULL). page_ref = code_insee (commune), id du feu (feu), 'france' (carte).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

CARTE_REF = "france"


def enqueue(conn: sqlite3.Connection, page_type: str, page_ref: str, *,
            stamp: str, trigger: str | None = None) -> bool:
    """Empile une page à régénérer si elle n'est pas déjà en attente. True si insérée."""
    cur = conn.execute(
        "INSERT INTO regen_queue (page_type, page_ref, enqueued_at, trigger) "
        "SELECT ?, ?, ?, ? WHERE NOT EXISTS ("
        "  SELECT 1 FROM regen_queue WHERE page_type=? AND page_ref=? AND processed_at IS NULL)",
        (page_type, page_ref, stamp, trigger, page_type, page_ref),
    )
    return cur.rowcount > 0


def enqueue_fire_update(conn: sqlite3.Connection, fire_event_id: int, communes: Iterable[str],
                        *, stamp: str, trigger: str | None = None, carte: bool = True) -> dict:
    """Empile la fiche feu, sa carte nationale, et les fiches des communes touchées."""
    n = 0
    n += enqueue(conn, "feu", str(fire_event_id), stamp=stamp, trigger=trigger)
    if carte:
        n += enqueue(conn, "carte", CARTE_REF, stamp=stamp, trigger=trigger)
    for code in communes:
        n += enqueue(conn, "commune", code, stamp=stamp, trigger=trigger)
    conn.commit()
    return {"enqueued": n}
