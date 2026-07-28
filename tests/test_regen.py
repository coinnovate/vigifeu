"""Alimentation de regen_queue — étape 9 (Lot 3, L3.5).

La file s'accumule (pas de consommateur avant le Lot 4). On vérifie l'émission des
bonnes pages et la déduplication des pages en attente.
"""

from __future__ import annotations

from vigifeu.engine.regen import enqueue, enqueue_fire_update

STAMP = "2026-07-22T12:00:00Z"


def _pending(conn):
    return {
        (r["page_type"], r["page_ref"])
        for r in conn.execute(
            "SELECT page_type, page_ref FROM regen_queue WHERE processed_at IS NULL"
        )
    }


def test_enqueue_dedup(db):
    conn, _ = db
    assert enqueue(conn, "commune", "33503", stamp=STAMP) is True
    assert enqueue(conn, "commune", "33503", stamp=STAMP) is False  # déjà en attente
    n = conn.execute("SELECT COUNT(*) AS n FROM regen_queue").fetchone()["n"]
    assert n == 1


def test_enqueue_reempile_apres_traitement(db):
    """Une page traitée peut être ré-empilée (nouvelle demande de régénération)."""
    conn, _ = db
    enqueue(conn, "commune", "33503", stamp=STAMP)
    conn.execute("UPDATE regen_queue SET processed_at=? WHERE page_ref='33503'", (STAMP,))
    assert enqueue(conn, "commune", "33503", stamp="2026-07-22T12:15:00Z") is True
    assert conn.execute("SELECT COUNT(*) AS n FROM regen_queue").fetchone()["n"] == 2


def test_enqueue_fire_update_pages(db):
    conn, _ = db
    enqueue_fire_update(conn, 7, ["33503", "33333"], stamp=STAMP, trigger="process_cycle")
    assert _pending(conn) == {
        ("feu", "7"),
        ("carte", "france"),
        ("commune", "33503"),
        ("commune", "33333"),
    }


def test_enqueue_fire_update_sans_carte(db):
    """Changement de vent : feu + communes, mais pas la carte nationale."""
    conn, _ = db
    enqueue_fire_update(conn, 7, ["33503"], stamp=STAMP, trigger="weather_obs", carte=False)
    pending = _pending(conn)
    assert ("feu", "7") in pending
    assert ("commune", "33503") in pending
    assert ("carte", "france") not in pending
