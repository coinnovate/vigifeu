"""Rejeu « France » synthétique — garde-fou des propriétés du §10.2.

La vraie fixture « 7 jours France » (Grande-Synthe, Fos, Port-Jérôme, triangle
mosellan en suspect_source_fixe ; aucun vrai feu rétrogradé) est absente du repo
(à constituer). En attendant, ce scénario synthétique valide les propriétés
structurantes du §10.2 sur des cas contrôlés :

  * des feux réels dans des régions différentes restent des événements DISTINCTS
    (pas de sur-fusion inter-régions — le risque de percolation) ;
  * des sources industrielles persistantes ⇒ suspect_source_fixe, jamais publiées ;
  * la présence d'une source fixe ne rétrograde pas un vrai feu voisin.
"""

from __future__ import annotations

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle

from .conftest import insert_hotspot

STAMP = "2026-07-27T00:00:00Z"

# Régions françaises éloignées (>> D_link) — chacune son foyer.
FOS = (43.45, 4.95)           # zone industrielle (torchères)
GRANDE_SYNTHE = (51.02, 2.30)  # zone industrielle
CORSE = (42.00, 9.10)          # vrai feu
MOSELLE = (49.20, 6.20)        # vrai feu


def _vrai_feu(conn, lat, lon):
    """Feu franc : 2 passages, 8 pixels, FRP soutenu ⇒ vegetation_confirme."""
    for i in range(8):
        insert_hotspot(conn, lat + i * 0.002, lon, "2026-07-22T12:00:00Z", frp=60.0, overpass_id=None)
    for i in range(8):
        insert_hotspot(conn, lat + i * 0.002, lon + 0.004, "2026-07-22T13:40:00Z", frp=70.0, overpass_id=None)


def _source_fixe(conn, lat, lon):
    """Torchère : même point, FRP faible, 15 jours ⇒ suspect_source_fixe."""
    for jour in range(1, 16):
        insert_hotspot(conn, lat, lon, f"2026-07-{jour:02d}T12:00:00Z", frp=4.0, overpass_id=None)


def _event_at(conn, lat, lon):
    # Tolérance serrée (~2 km) : chaque foyer synthétique est isolé, on ne veut pas
    # attraper un foyer voisin.
    return conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE ABS(lat-?)<0.018 AND ABS(lon-?)<0.018 "
        "AND fire_event_id IS NOT NULL LIMIT 1", (lat, lon)
    ).fetchone()["fire_event_id"]


def _qual(conn, eid):
    return conn.execute("SELECT qualification FROM fire_event WHERE id=?", (eid,)).fetchone()["qualification"]


def test_rejeu_france_synthetique(db):
    conn, config = db
    _vrai_feu(conn, *CORSE)
    _vrai_feu(conn, *MOSELLE)
    _source_fixe(conn, *FOS)
    _source_fixe(conn, *GRANDE_SYNTHE)
    build_overpasses(conn, config)
    process_cycle(conn, config, stamp=STAMP)

    corse = _event_at(conn, *CORSE)
    moselle = _event_at(conn, *MOSELLE)
    fos = _event_at(conn, *FOS)
    gs = _event_at(conn, *GRANDE_SYNTHE)

    # Quatre foyers, quatre événements distincts (aucune sur-fusion inter-régions).
    assert len({corse, moselle, fos, gs}) == 4

    # Les vrais feux sont confirmés ; les sources industrielles restent suspectes.
    assert _qual(conn, corse) == "vegetation_confirme"
    assert _qual(conn, moselle) == "vegetation_confirme"
    assert _qual(conn, fos) == "suspect_source_fixe"
    assert _qual(conn, gs) == "suspect_source_fixe"

    # Seuls les feux réels reçoivent une version (publiables) ; pas les sources fixes.
    assert conn.execute(
        "SELECT COUNT(DISTINCT fire_event_id) AS n FROM fire_event_version"
    ).fetchone()["n"] == 2


def test_source_fixe_ne_retrograde_pas_un_feu_voisin(db):
    """Un vrai feu à côté d'une torchère (mais au-delà de D_link) reste confirmé."""
    conn, config = db
    _source_fixe(conn, *FOS)
    # Vrai feu à ~5,5 km de la torchère (au-delà de D_link : événement distinct).
    _vrai_feu(conn, FOS[0] + 0.050, FOS[1])
    build_overpasses(conn, config)
    process_cycle(conn, config, stamp=STAMP)

    torche = _event_at(conn, *FOS)
    feu = _event_at(conn, FOS[0] + 0.050, FOS[1])
    assert torche != feu
    assert _qual(conn, torche) == "suspect_source_fixe"
    assert _qual(conn, feu) == "vegetation_confirme"
