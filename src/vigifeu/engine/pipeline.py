"""Orchestration d'un cycle de traitement (Spec 02 §3, étapes 3→7).

Enchaîne, sur les seuls feux touchés par le cycle (jamais de recalcul global en
nominal) :

  3. marquage sources fixes   → mark_fixed_sources
  4. clustering               → cluster_new_hotspots
  5. qualification            → qualify_events (+ promotion des sources fixes)
  6. version                  → create_version (économie §5 : pas les suspects stables)
  7. cellules                 → rebuild_cells
  (8. relations communes — Lot 3 ; 9. régénération — Lot 4)
  + transitions de cycle de vie → apply_lifecycle

La construction des passages (étape 2) est faite en amont par le scheduler
(build_overpasses après le fetch). `reset_interpretation` + un `process_cycle`
rejouent tout l'historique brut (P2) — c'est le rejeu Saumos.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from vigifeu.engine.cells import rebuild_cells
from vigifeu.engine.cluster import apply_lifecycle, cluster_new_hotspots
from vigifeu.engine.fixed_source import mark_fixed_sources, promote_candidates
from vigifeu.engine.overpass import rebuild_overpasses
from vigifeu.engine.qualify import qualify_events
from vigifeu.engine.regen import enqueue_fire_update
from vigifeu.engine.relations import compute_commune_relations
from vigifeu.engine.version import create_version

_CONFIRME = "vegetation_confirme"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data_clock(conn: sqlite3.Connection) -> str | None:
    """Horloge des données = dernière acquisition connue (référence des transitions
    et de l'état des cellules ; en rejeu, jamais l'heure murale)."""
    row = conn.execute("SELECT MAX(last_acq_at) AS m FROM fire_event").fetchone()
    return row["m"] if row and row["m"] else None


def process_cycle(conn: sqlite3.Connection, config: dict, *, stamp: str | None = None,
                  clock: str | None = None, trigger_run_id: int | None = None) -> dict:
    """Traite les hotspots nouvellement disponibles. Idempotent : sans nouveauté, no-op."""
    stamp = stamp or _now_iso()

    marked = mark_fixed_sources(conn, config)["marked"]
    cl = cluster_new_hotspots(conn, config, stamp=stamp)
    qual = qualify_events(conn, config, cl["touched"], stamp=stamp)
    promoted = promote_candidates(conn, config, stamp=stamp)

    clock = clock or _data_clock(conn)

    versioned: list[int] = []
    relations_opened = relations_closed = 0
    for eid in cl["touched"]:
        fe = conn.execute(
            "SELECT qualification, lifecycle FROM fire_event WHERE id=?", (eid,)
        ).fetchone()
        if fe is None or fe["lifecycle"] == "fusionne":
            continue
        # Économie de versionnage (§5, §5.2) : seuls les vegetation_confirme ont une
        # fiche et une relecture de propagation. On ne verse une version que pour
        # eux, ou pour un feu DÉJÀ versionné (donc publié) qui change d'état — une
        # rétrogradation confirme → faux_positif s'affiche explicitement (§5.1).
        # Un suspect qui reste suspect ne crée jamais de version.
        has_version = conn.execute(
            "SELECT 1 FROM fire_event_version WHERE fire_event_id=? LIMIT 1", (eid,)
        ).fetchone() is not None
        if fe["qualification"] == _CONFIRME or (eid in qual["changed"] and has_version):
            rebuild_cells(conn, config, eid, clock=clock)
            vid = create_version(conn, config, eid, stamp=stamp,
                                 trigger_run_id=trigger_run_id, reprise=(eid in cl["reprises"]))
            # Étape 8 (§7) : relations emprise/a_moins_de_X sur l'union des cellules,
            # historisées. No-op si aucune commune chargée (garde le Lot 2 vert).
            rel = compute_commune_relations(conn, config, eid, version_id=vid, stamp=stamp)
            relations_opened += rel["opened"]
            relations_closed += rel["closed"]
            # Étape 9 (§8) : émettre les pages impactées (feu + carte + communes touchées).
            enqueue_fire_update(conn, eid, rel["communes"], stamp=stamp,
                                trigger=f"run:{trigger_run_id}" if trigger_run_id else "process_cycle")
            versioned.append(eid)

    life = apply_lifecycle(conn, config, clock=clock)

    return {
        "marked": marked,
        "created": cl["created"],
        "attached": cl["attached"],
        "merged": cl["merged"],
        "reprises": len(cl["reprises"]),
        "requalified": len(qual["changed"]),
        "promoted": len(promoted),
        "versioned": len(versioned),
        "relations_opened": relations_opened,
        "relations_closed": relations_closed,
        "lifecycle": life,
    }


def reset_interpretation(conn: sqlite3.Connection, config: dict) -> None:
    """Efface toute l'interprétation et reconstruit les passages (P2, rejeu).

    Les observations (hotspot_raw) et le registre fixed_source (semi-manuel) sont
    conservés ; seules les colonnes d'interprétation et les tables dérivées d'un feu
    sont remises à zéro. Un `process_cycle` ensuite reconstruit tout à l'identique.

    Détacher/supprimer TOUT ce qui référence fire_event est indispensable, sinon la
    contrainte FK saute (le bug ne sortait pas tant que le daemon n'écrivait rien — cf.
    fix threading Lot 5 ; désormais `hotspot_raw.fire_event_id` et `weather_obs` sont
    peuplés). On coupe l'enforcement le temps du wipe (ordre et auto-références
    indifférents), puis un `foreign_key_check` garantit qu'aucune orpheline ne subsiste.
    """
    conn.commit()  # aucune transaction ouverte : PRAGMA foreign_keys serait ignoré sinon
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # Observations conservées : on ne coupe que leur lien vers l'interprétation effacée.
        conn.execute("UPDATE hotspot_raw SET fire_event_id=NULL, fixed_source_id=NULL")
        conn.execute("UPDATE geo_detection_raw SET confirmed_by_fire_event_id=NULL")
        # Interprétation + dérivées d'un feu (la météo a fire_event_id NOT NULL donc n'est
        # pas NULLable → supprimée ; elle sera rééchantillonnée au prochain cycle).
        conn.execute("DELETE FROM weather_forecast WHERE fire_event_id IS NOT NULL")
        for table in ("fe_hotspot", "weather_obs", "fire_event_version",
                      "fire_cell_state", "fe_fe_rel", "fe_commune_rel", "fire_event"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    orphelines = conn.execute("PRAGMA foreign_key_check").fetchall()
    if orphelines:
        raise RuntimeError(f"reset_interpretation : références orphelines restantes {orphelines}")
    rebuild_overpasses(conn, config)
