"""Déduplication des communes par relation sur la fiche feu (bug doublons direction_vent)."""

from __future__ import annotations

from vigifeu.generate.feu import _groupes_communes


def _rel(rel_type, code, slug, nom, vf, vt, dist=None):
    return {"rel_type": rel_type, "distance_km": dist, "valid_from": vf, "valid_to": vt,
            "code_insee": code, "slug": slug, "nom": nom}


def test_direction_vent_actuelle_seulement_et_dedup():
    rels = [
        # Audenge : 2 relations fermées + 1 en cours → une seule ligne, en cours (sans intervalle)
        _rel("direction_vent", "33019", "audenge", "Audenge", "2026-07-29T00:00:00Z", "2026-07-29T06:00:00Z"),
        _rel("direction_vent", "33019", "audenge", "Audenge", "2026-07-30T00:00:00Z", "2026-07-30T06:00:00Z"),
        _rel("direction_vent", "33019", "audenge", "Audenge", "2026-07-30T12:00:00Z", None),
        # Lège : uniquement une relation FERMÉE → exclue (pas « actuelle »)
        _rel("direction_vent", "33236", "lege", "Lège", "2026-07-29T00:00:00Z", "2026-07-29T06:00:00Z"),
    ]
    coms = _groupes_communes(rels, ["direction_vent"])[0]["communes"]
    assert [c["nom"] for c in coms] == ["Audenge"]          # dédup + Lège (fermée) exclue
    assert coms[0]["interval"] is None                       # en cours → pas d'intervalle


def test_proximite_fusionne_les_intervalles():
    rels = [
        _rel("emprise_dans_commune", "33001", "a", "A", "2026-07-22T00:00:00Z", "2026-07-24T00:00:00Z"),
        _rel("emprise_dans_commune", "33001", "a", "A", "2026-07-25T00:00:00Z", "2026-07-27T00:00:00Z"),
    ]
    coms = _groupes_communes(rels, ["emprise_dans_commune"])[0]["communes"]
    assert len(coms) == 1
    assert coms[0]["interval"] == "concernée du 22/07/2026 au 27/07/2026"   # fenêtre min→max
