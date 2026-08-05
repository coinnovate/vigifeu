"""Tests du clustering spatio-temporel incrémental (engine/cluster.py, Spec 02 §4).

Deux registres :
- des scénarios synthétiques contrôlés (création, fusion, reprise, cycle de vie) ;
- un rejeu ciblé sur la fixture Saumos réelle (Gironde ouest) pour le jalon :
  un feu unique, l'événement du 20/07 distinct, first_acq_at = 22/07 12:32Z.
"""

from __future__ import annotations

from vigifeu.engine.cluster import apply_lifecycle, cluster_new_hotspots

from .conftest import insert_hotspot, load_saumos_hotspots

STAMP = "2026-07-27T00:00:00Z"

# ------------------------------------------------------------------ synthétique


def _event_of(conn, hotspot_id):
    return conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE id=?", (hotspot_id,)
    ).fetchone()["fire_event_id"]


def test_creation_et_rattachement(db):
    """Deux pixels proches et rapprochés dans le temps = un feu ; un pixel lointain = un autre."""
    conn, config = db
    a = insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:32:00Z")
    b = insert_hotspot(conn, 44.901, -1.021, "2026-07-22T12:42:00Z")  # ~150 m, +10 min
    c = insert_hotspot(conn, 45.100, -1.020, "2026-07-22T12:42:00Z")  # ~22 km au nord

    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    assert res["created"] == 2
    assert res["attached"] == 1
    assert _event_of(conn, a) == _event_of(conn, b)
    assert _event_of(conn, c) != _event_of(conn, a)
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"] == 2


def test_deux_foyers_distants_restent_separes(db):
    """Saumos (44.90,-1.02) et le foyer du 20/07 (44.80,-1.10), à ~12,6 km, ne
    fusionnent jamais (D_link = 1,5 km) — cœur du jalon §10.1."""
    conn, config = db
    saumos = insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:32:00Z")
    j20 = insert_hotspot(conn, 44.800, -1.100, "2026-07-22T12:32:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    assert _event_of(conn, saumos) != _event_of(conn, j20)


def test_fusion_le_plus_ancien_absorbe(db):
    """Deux feux distincts qu'un pixel intermédiaire relie fusionnent ; le plus
    ancien absorbe, l'autre passe `fusionne` avec merged_into + fe_fe_rel."""
    conn, config = db
    # Feu A (ancien), feu B (récent), à ~2,4 km — distincts au départ.
    a = insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z")
    b = insert_hotspot(conn, 44.900, -0.990, "2026-07-22T12:30:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    ea, eb = _event_of(conn, a), _event_of(conn, b)
    assert ea != eb

    # Pixel intermédiaire à < 1,5 km des deux ⇒ fusion.
    insert_hotspot(conn, 44.900, -1.005, "2026-07-22T13:00:00Z")
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    assert res["merged"] == 1

    absorber, absorbed = (ea, eb) if ea < eb else (eb, ea)
    row = conn.execute("SELECT lifecycle, merged_into FROM fire_event WHERE id=?", (absorbed,)).fetchone()
    assert row["lifecycle"] == "fusionne"
    assert row["merged_into"] == absorber
    rel = conn.execute(
        "SELECT rel_type FROM fe_fe_rel WHERE fire_event_id=? AND related_fire_event_id=?",
        (absorbed, absorber),
    ).fetchone()
    assert rel["rel_type"] == "fusionne_dans"
    # first_acq de l'absorbeur = la plus ancienne des deux origines (§4.4).
    assert conn.execute(
        "SELECT first_acq_at FROM fire_event WHERE id=?", (absorber,)
    ).fetchone()["first_acq_at"] == "2026-07-22T12:00:00Z"


def test_reprise_rouvre_le_meme_evenement(db):
    """Un pixel au même endroit 32 h plus tard (T_silence < 32 h < T_gap) rouvre le
    feu plus_detecte : même événement, reprise signalée, lifecycle repassé à actif."""
    conn, config = db
    a = insert_hotspot(conn, 44.900, -1.020, "2026-07-20T12:00:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    ev = _event_of(conn, a)
    # Le feu s'est tu : on le passe plus_detecte (comme le ferait la passe horaire).
    apply_lifecycle(conn, config, clock="2026-07-21T12:00:00Z")
    assert conn.execute("SELECT lifecycle FROM fire_event WHERE id=?", (ev,)).fetchone()["lifecycle"] == "plus_detecte"

    b = insert_hotspot(conn, 44.900, -1.020, "2026-07-21T20:00:00Z")  # +32 h
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    assert _event_of(conn, b) == ev
    assert ev in res["reprises"]
    assert conn.execute("SELECT lifecycle FROM fire_event WHERE id=?", (ev,)).fetchone()["lifecycle"] == "actif"


def test_au_dela_de_candidature_nouvel_evenement_proche_de(db):
    """Hors fenêtre de candidature (g ≥ T_gap), le même endroit crée un NOUVEL
    événement, relié à l'ancien foyer éteint par `proche_de` (contexte de fiche)."""
    conn, config = db
    a = insert_hotspot(conn, 44.900, -1.020, "2026-07-10T12:00:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    ancien = _event_of(conn, a)

    b = insert_hotspot(conn, 44.900, -1.020, "2026-07-20T12:00:00Z")  # +10 j
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    nouveau = _event_of(conn, b)
    assert nouveau != ancien
    assert res["created"] == 1
    rel = conn.execute(
        "SELECT rel_type FROM fe_fe_rel WHERE fire_event_id=? AND related_fire_event_id=?",
        (nouveau, ancien),
    ).fetchone()
    assert rel["rel_type"] == "proche_de"


def test_cycle_de_vie_transitions(db):
    """actif → plus_detecte (T_silence) → archive (T_reprise), contre l'horloge."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-20T12:00:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)

    # +12 h : encore actif.
    apply_lifecycle(conn, config, clock="2026-07-21T00:00:00Z")
    assert conn.execute("SELECT lifecycle FROM fire_event").fetchone()["lifecycle"] == "actif"
    # +30 h : plus_detecte.
    r = apply_lifecycle(conn, config, clock="2026-07-21T18:00:00Z")
    assert r["to_plus_detecte"] == 1
    assert conn.execute("SELECT lifecycle FROM fire_event").fetchone()["lifecycle"] == "plus_detecte"
    # +8 j : archive.
    r = apply_lifecycle(conn, config, clock="2026-07-28T12:00:00Z")
    assert r["to_archive"] == 1
    assert conn.execute("SELECT lifecycle FROM fire_event").fetchone()["lifecycle"] == "archive"


def _queued(conn, page_type, ref):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM regen_queue "
        "WHERE page_type=? AND page_ref=? AND processed_at IS NULL",
        (page_type, ref),
    ).fetchone()["n"]


def test_transition_reenfile_fiche_publiee(db):
    """§4.5 « dernière régénération » : un feu PUBLIÉ qui se tait ré-enfile ses pages.

    → plus_detecte : seule la fiche change (« plus détecté depuis… »), pas la carte
    ni les communes (le feu reste en situation en cours). → archive : le feu quitte
    la carte et la situation en cours ⇒ fiche + carte + communes à relation ouverte.
    """
    conn, config = db
    a = insert_hotspot(conn, 44.900, -1.020, "2026-07-20T12:00:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    fe_id = _event_of(conn, a)
    # Simule la publication (public_id) + une relation commune ouverte.
    conn.execute("UPDATE fire_event SET public_id='2026-test' WHERE id=?", (fe_id,))
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom) VALUES ('33333', 'le-porge', 'Le Porge')"
    )
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from) "
        "VALUES (?, '33333', 'emprise_dans_commune', ?)",
        (fe_id, STAMP),
    )
    conn.commit()

    # → plus_detecte : seule la fiche.
    apply_lifecycle(conn, config, clock="2026-07-21T18:00:00Z", stamp=STAMP)
    assert _queued(conn, "feu", str(fe_id)) == 1
    assert _queued(conn, "carte", "france") == 0
    assert _queued(conn, "commune", "33333") == 0

    # → archive : fiche (toujours en attente) + carte + commune.
    apply_lifecycle(conn, config, clock="2026-07-28T12:00:00Z", stamp=STAMP)
    assert _queued(conn, "feu", str(fe_id)) == 1
    assert _queued(conn, "carte", "france") == 1
    assert _queued(conn, "commune", "33333") == 1


def test_transition_feu_non_publie_n_enfile_rien(db):
    """Un suspect (sans public_id) n'a pas de page : aucune régénération émise."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-20T12:00:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    apply_lifecycle(conn, config, clock="2026-07-28T12:00:00Z", stamp=STAMP)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM regen_queue"
    ).fetchone()["n"] == 0


def test_idempotence(db):
    """Relancer le clustering sans nouveau hotspot = no-op."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:32:00Z")
    cluster_new_hotspots(conn, config, stamp=STAMP)
    n_ev = conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"]
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    assert res == {"created": 0, "attached": 0, "merged": 0, "reprises": set(), "touched": set()}
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"] == n_ev


# ------------------------------------------------------------------ rejeu réel


def test_saumos_reel_un_feu_distinct_du_20juillet(db):
    """Rejeu sur la Gironde ouest réelle : le feu de Saumos est un événement unique,
    et le foyer du 20/07 (12,6 km) en est distinct.

    first_acq_at = 2026-07-22 11:55Z : la première détection du cluster (passage
    NOAA-21), conformément à la Spec §4.4 (« la date de première détection est celle
    du cluster spatio-temporel »). Le 12:32Z du jalon d'origine est la première
    *confirmation* (2ᵉ passage, SNPP) ; la détection NOAA-21 de 11:55, ajoutée à la
    fixture depuis, est 37 min plus précoce — un gain de latence, pas un écart."""
    from vigifeu.engine.overpass import build_overpasses

    conn, config = db
    load_saumos_hotspots(conn, bbox=(44.5, 45.3, -1.30, -0.30))
    build_overpasses(conn, config)
    cluster_new_hotspots(conn, config, stamp=STAMP)

    # L'événement qui porte les détections du 22/07 12:32 à ~44.90,-1.02.
    saumos_hs = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw "
        "WHERE acq_at='2026-07-22T12:32:00Z' AND lat BETWEEN 44.88 AND 44.92 "
        "AND lon BETWEEN -1.05 AND -0.99 AND fire_event_id IS NOT NULL"
    ).fetchall()
    assert saumos_hs, "détection Saumos du 22/07 12:32 non rattachée"
    saumos_ids = {r["fire_event_id"] for r in saumos_hs}
    assert len(saumos_ids) == 1, "le cœur de Saumos doit être un seul événement"
    saumos_id = saumos_ids.pop()

    # first_acq_at contractuel : le plus ancien du cluster (NOAA-21, 11:55Z).
    fe = conn.execute(
        "SELECT first_acq_at, lifecycle FROM fire_event WHERE id=?", (saumos_id,)
    ).fetchone()
    assert fe["first_acq_at"] == "2026-07-22T11:55:00Z"

    # Le foyer du 20/07 (44.80,-1.10) est un AUTRE événement.
    j20 = conn.execute(
        "SELECT DISTINCT fire_event_id FROM hotspot_raw "
        "WHERE acq_at LIKE '2026-07-20%' AND lat BETWEEN 44.78 AND 44.82 "
        "AND lon BETWEEN -1.12 AND -1.08 AND fire_event_id IS NOT NULL"
    ).fetchall()
    assert j20, "foyer du 20/07 non rattaché"
    assert saumos_id not in {r["fire_event_id"] for r in j20}
