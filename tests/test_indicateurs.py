"""Bandeau d'indicateurs de la fiche feu : statuts par seuils, FRP jamais « faible »."""

from __future__ import annotations

from vigifeu.generate.indicateurs import indicateurs_feu


def test_indicateurs_feu_actif_statuts(db):
    conn, config = db
    wobs = {"wind_speed_kmh": 35, "wind_gusts_kmh": 60, "wind_dir_deg": 247.5,
            "temp_c": 32, "rh_pct": 20}
    latest = {"frp_total_last_pass_mw": 285, "area_ha_estimee": 1200}
    t = {x["label"]: x for x in indicateurs_feu(
        config, wobs=wobs, latest=latest, danger_foret="3", n_communes=8, actif=True)}

    assert t["Danger forêt"]["statut"] == "eleve"            # niveau 3/4 (officiel)
    assert t["Vent"]["statut"] == "modere" and "OSO" in t["Vent"]["niveau"]   # 35 km/h
    assert t["Température"]["statut"] == "eleve"             # 32 °C (30–35)
    assert t["Humidité de l'air"]["statut"] == "critique"   # 20 % (< 25) → très sec
    assert t["Puissance thermique"]["statut"] == "eleve"    # 285 MW (50–500) → soutenu
    assert t["Surface estimée"]["statut"] == "neutre"
    assert t["Communes concernées"]["statut"] == "neutre" and t["Communes concernées"]["valeur"] == "8"


def test_frp_jamais_faible(db):
    conn, config = db
    t = {x["label"]: x for x in indicateurs_feu(
        config, wobs=None, latest={"frp_total_last_pass_mw": 30, "area_ha_estimee": None},
        danger_foret=None, n_communes=0, actif=True)}
    # un foyer de 30 MW = « Modéré », jamais « faible »/vert
    assert t["Puissance thermique"]["statut"] == "modere"
    assert "Puissance thermique" in t and "Surface estimée" not in t   # area None → pas de tuile


def test_archive_pas_de_meteo_ni_frp(db):
    conn, config = db
    # feu archivé : pas de météo live, pas de FRP (gated actif) → seulement l'ampleur.
    t = [x["label"] for x in indicateurs_feu(
        config, wobs=None, latest={"frp_total_last_pass_mw": 9, "area_ha_estimee": 500},
        danger_foret=None, n_communes=12, actif=False)]
    assert t == ["Surface estimée", "Communes concernées"]
