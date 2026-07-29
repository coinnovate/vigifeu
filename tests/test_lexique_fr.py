"""Tests unitaires du lexique contractuel (Spec 03 §2).

Le lexique est le contrat : chaque formulation est figée ici. Ces tests sont la
première ligne de défense avant le lint HTML (Spec 04 §9.1) et le golden file.
Aucune base, aucun réseau — fonctions pures.
"""

from __future__ import annotations

import pytest

from vigifeu.lexique import fr


# --- helpers de formatage ---------------------------------------------------

def test_horodatage_variantes():
    assert fr.horodatage("2026-07-22T12:32:00Z") == "22/07/2026 à 14:32"
    assert fr.horodatage("2026-07-22 12:32:00") == "22/07/2026 à 14:32"
    assert fr.horodatage("2026-07-22T12:32:00+00:00") == "22/07/2026 à 14:32"
    assert fr.date_fr("2026-07-22T11:55:00Z") == "22/07/2026"
    assert fr.heure_fr("2026-07-22T11:55:00Z") == "13:55"


def test_nombre_fr():
    assert fr.nombre_fr(285) == "285"
    assert fr.nombre_fr(5.5, 1) == "5,5"
    assert fr.nombre_fr(3400) == "3 400"
    assert fr.nombre_fr(373.4, 0) == "373"


def test_cardinal_fr():
    assert fr.cardinal_fr(0) == "N"
    assert fr.cardinal_fr(90) == "E"
    assert fr.cardinal_fr(247.5, 16) == "OSO"
    assert fr.cardinal_fr(45, 8) == "NE"
    assert fr.cardinal_long_fr(0) == "le nord"
    assert fr.cardinal_long_fr(45) == "le nord-est"
    assert fr.cardinal_long_fr(90) == "l'est"


def test_liste_fr():
    assert fr._liste_fr([]) == ""
    assert fr._liste_fr(["Saumos"]) == "Saumos"
    assert fr._liste_fr(["Le Porge", "Lacanau"]) == "Le Porge et Lacanau"
    assert fr._liste_fr(["Le Porge", "Lacanau", "Le Temple"]) == "Le Porge, Lacanau et Le Temple"


# --- 2.1 cycle de vie -------------------------------------------------------

def test_libelle_cycle_de_vie_actif():
    assert (
        fr.libelle_cycle_de_vie("actif", detecte_dernier_passage=True,
                                heure_dernier_passage="2026-07-24T02:10:00Z")
        == "Détecté au dernier passage satellite (04:10)"
    )
    assert (
        fr.libelle_cycle_de_vie("actif", detecte_dernier_passage=False,
                                heure_dernier_passage="2026-07-24T02:10:00Z")
        == "Aucune détection au dernier passage (04:10) — le suivi continue"
    )


def test_libelle_cycle_de_vie_plus_detecte():
    assert (
        fr.libelle_cycle_de_vie("plus_detecte", heures_depuis=30,
                                dernier_hotspot="2026-07-25T13:40:00Z")
        == "Plus détecté depuis 30 heures (dernier point chaud : 25/07/2026 à 15:40)"
    )


def test_cycle_de_vie_interdits():
    # « éteint »/« en cours »/« hors de contrôle » ne doivent jamais sortir.
    phrases = [
        fr.libelle_cycle_de_vie("actif", detecte_dernier_passage=True,
                                heure_dernier_passage="2026-07-24T02:10:00Z"),
        fr.libelle_cycle_de_vie("plus_detecte", heures_depuis=30,
                                dernier_hotspot="2026-07-25T13:40:00Z"),
        fr.libelle_zone_cellule("plus_detecte"),
    ]
    for p in phrases:
        for terme in ("éteint", "en cours", "hors de contrôle", "maîtrisé"):
            assert terme not in p.lower()


def test_libelle_cycle_de_vie_archive_delegue():
    with pytest.raises(ValueError):
        fr.libelle_cycle_de_vie("archive")
    assert fr.bandeau_archive("2026-07-26T00:00:00Z") == "Feu archivé — dernière détection le 26/07/2026"


def test_periode_suivi_et_badge_confiance():
    assert (
        fr.phrase_periode_suivi("2026-07-22T11:55:00Z", "2026-07-26T13:40:00Z")
        == "Feu suivi du 22/07/2026 au 26/07/2026"
    )
    assert fr.badge_confiance("confirme") == "Détection confirmée"
    assert fr.badge_confiance("probable") == "Détection probable"


def test_badge_et_zones():
    assert fr.badge_cycle_de_vie("actif") == "Actif"
    assert fr.badge_cycle_de_vie("archive") == "Archivé"
    assert fr.libelle_zone_cellule("front_actif") == "Zone détectée au dernier passage"
    assert fr.libelle_zone_cellule("recent") == "Zone détectée il y a moins de 24 h"
    assert fr.libelle_zone_cellule("plus_detecte") == "Zone plus détectée depuis plus de 24 h"


def test_mention_reprise_pas_de_terme_sdis():
    p = fr.mention_reprise("2026-07-25T00:00:00Z")
    assert p == "Nouvelles détections dans une zone précédemment silencieuse depuis le 25/07/2026"
    assert "reprise" not in p.lower()


# --- 2.2 dynamique ----------------------------------------------------------

def test_phrase_progression():
    assert (
        fr.phrase_progression(5.9, 0, passage_a="2026-07-24T02:00:00Z",
                              passage_b="2026-07-25T02:00:00Z")
        == "Le front de détection a progressé d'environ 5,9 km vers le nord "
           "entre le 24/07/2026 à 04:00 et le 25/07/2026 à 04:00"
    )


def test_phrase_frp_et_comparee():
    assert (
        fr.phrase_frp(285, type_passage="nuit", date="2026-07-24T02:00:00Z")
        == "Puissance thermique (FRP) : 285 mégawatts au passage de nuit du 24/07/2026"
    )
    p = fr.phrase_frp_comparee(28, 285, type_courant="nuit", type_precedent="nuit",
                               date="2026-07-25T02:00:00Z")
    assert "÷10,2" in p
    assert "au passage de nuit du 25/07/2026" in p


def test_phrase_frp_garde_jour_nuit():
    with pytest.raises(ValueError):
        fr.phrase_frp_comparee(100, 200, type_courant="jour", type_precedent="nuit",
                               date="2026-07-25T14:00:00Z")


def test_phrase_emprise_et_surface():
    assert (
        fr.phrase_emprise_estimee(373)
        == "Emprise estimée d'après les détections : environ 373 ha "
           "(estimation satellite, non officielle)"
    )
    assert (
        fr.phrase_surface_officielle(3400, "la préfecture de Gironde", "2026-07-26T00:00:00Z")
        == "Surface parcourue annoncée par la préfecture de Gironde : 3 400 ha (26/07/2026)"
    )


# --- 2.3 vent ---------------------------------------------------------------

def test_phrase_vent():
    assert (
        fr.phrase_vent(247.5, 35, 60, provider="Open-Meteo", heure="2026-07-24T12:00:00Z")
        == "Vent OSO 35 km/h, rafales 60 km/h — mesure Open-Meteo de 14:00"
    )


def test_phrase_meteo():
    p = fr.phrase_meteo(temp_c=32, rh_pct=25, dir_origine_deg=247.5, v_kmh=35, rafales_kmh=60,
                        precip_1h=0, provider="Open-Meteo", heure="2026-07-24T12:00:00Z")
    assert p == ("Conditions au foyer (mesure Open-Meteo de 14:00) : "
                 "32 °C, humidité 25 %, vent OSO 35 km/h (rafales 60 km/h), 0 mm de pluie sur la dernière heure")
    # champs manquants omis ; None si aucune donnée
    partiel = fr.phrase_meteo(temp_c=30, provider="Open-Meteo", heure="2026-07-24T12:00:00Z")
    assert partiel == "Conditions au foyer (mesure Open-Meteo de 14:00) : 30 °C"
    assert fr.phrase_meteo(provider="x", heure="2026-07-24T12:00:00Z") is None


def test_phrase_vent_communes_aval():
    # vent d'ouest (270° d'origine) → souffle vers l'est.
    p = fr.phrase_vent_communes(270, ["Le Porge", "Lacanau"], heure="2026-07-24T12:00:00Z")
    assert p == ("Le vent de 14:00 souffle en direction l'est ; "
                 "dans cette direction se trouvent Le Porge et Lacanau")


def test_legende_cone_contractuelle():
    assert "ne représente ni une prévision ni une zone de propagation" in fr.LEGENDE_CONE
    assert "menac" not in fr.LEGENDE_CONE.lower()


# --- 2.4 prévisions ---------------------------------------------------------

def test_phrase_prevision():
    contenu = fr.contenu_pluie(12, 48, 70)
    assert (
        fr.phrase_prevision("Open-Meteo", "AROME", "2026-07-24T06:00:00Z", contenu)
        == "Prévision Open-Meteo/AROME (run de 08:00) : "
           "12 mm de pluie attendus sur la zone d'ici 48 h (probabilité 70 %)"
    )


# --- 2.5 barèmes sécheresse -------------------------------------------------

DC_SEUILS = [100, 300, 500]
FWI_SEUILS = [5.2, 11.2, 21.3, 38.0, 50.0]
SIM_SEUILS = [10, 40, 60]


def test_classe_dc():
    assert fr.classe_dc(50, DC_SEUILS) == "faible"
    assert fr.classe_dc(200, DC_SEUILS) == "modérée"
    assert fr.classe_dc(400, DC_SEUILS) == "élevée"
    assert fr.classe_dc(600, DC_SEUILS) == "très élevée"
    assert fr.classe_dc(100, DC_SEUILS) == "modérée"  # borne incluse dans la classe sup.
    assert fr.phrase_dc(600, DC_SEUILS) == "Sécheresse profonde du terrain : très élevée"


def test_classe_fwi_six_classes():
    assert fr.classe_fwi(3, FWI_SEUILS) == "très faible"
    assert fr.classe_fwi(8, FWI_SEUILS) == "faible"
    assert fr.classe_fwi(15, FWI_SEUILS) == "modéré"
    assert fr.classe_fwi(30, FWI_SEUILS) == "élevé"
    assert fr.classe_fwi(45, FWI_SEUILS) == "très élevé"
    assert fr.classe_fwi(70, FWI_SEUILS) == "extrême"
    assert (
        fr.phrase_fwi(70, "2026-07-24T00:00:00Z", FWI_SEUILS)
        == "Danger météorologique d'incendie (24/07/2026) : extrême (indice FWI, Copernicus/EFFIS)"
    )


def test_meteo_forets_passthrough():
    assert (
        fr.phrase_meteo_forets("rouge", "33", "2026-07-24T00:00:00Z")
        == "Météo des forêts (Météo-France, 24/07/2026) : niveau rouge pour le département 33"
    )


def test_classe_sim():
    assert fr.classe_sim(5, SIM_SEUILS) == "très inférieure"
    assert fr.classe_sim(25, SIM_SEUILS) == "inférieure"
    assert fr.classe_sim(50, SIM_SEUILS) == "proche"
    assert fr.classe_sim(80, SIM_SEUILS) == "supérieure"
    assert (
        fr.phrase_sim(5, "2026-07-20T00:00:00Z", SIM_SEUILS)
        == "Humidité des sols : très inférieure à la normale de saison (SIM, décade du 20/07/2026)"
    )


def test_phrase_vigieau_declaree():
    assert (
        fr.phrase_vigieau("alerte_renforcee", "2026-06-15T00:00:00Z")
        == "Commune en alerte renforcée sécheresse par arrêté préfectoral depuis le 15/06/2026"
    )
    assert (
        fr.phrase_vigieau("crise", "2026-06-15T00:00:00Z")
        == "Commune en crise sécheresse par arrêté préfectoral depuis le 15/06/2026"
    )


# --- 2.6 latence / 2.7 attributions ----------------------------------------

def test_commune_situation():
    assert (
        fr.commune_aucun_feu("2026-07-28T05:00:00Z")
        == "Aucun incendie suivi actuellement sur la commune ou à moins de 20 km "
           "(dernière observation satellite : 28/07/2026 à 07:00)"
    )
    assert fr.commune_relation_emprise("Saumos") == "L'incendie de Saumos a une emprise sur la commune"
    assert (fr.commune_relation_distance("Saumos", 3.4)
            == "L'incendie de Saumos est suivi à 3,4 km de la limite communale")
    assert "direction actuelle du vent" in fr.commune_relation_vent("Saumos", "2026-07-24T12:00:00Z")
    # jamais « aucun risque » (Spec 03 §4.8)
    assert "risque" not in fr.commune_aucun_feu("2026-07-28T05:00:00Z").lower()


def test_commune_historique_et_intervalles():
    assert (
        fr.commune_feu_suivi_intervalle("Saumos", "2026-07-22T00:00:00Z", "2026-07-26T00:00:00Z")
        == "Concernée par le feu de Saumos du 22/07/2026 au 26/07/2026"
    )
    assert fr.commune_feu_suivi_intervalle("Saumos", "2026-07-22T00:00:00Z", None) \
        == "Concernée par le feu de Saumos depuis le 22/07/2026"
    assert fr.commune_historique_bdiff(0, None, 2006) == "Aucun incendie de végétation recensé depuis 2006 (BDIFF)"
    assert (fr.commune_historique_bdiff(3, 1250, 2006)
            == "Depuis 2006, 3 incendies recensés (BDIFF) — surface cumulée 1 250 ha")
    assert fr.commune_historique_bdiff(1, 40, 2006).startswith("Depuis 2006, 1 incendie recensé (BDIFF)")


def test_bloc_latence():
    p = fr.bloc_latence("2026-07-24T12:00:00Z")
    assert "délai de traitement de 1 à 3 h" in p
    assert "Dernière observation intégrée : 24/07/2026 à 14:00." in p


def test_bloc_attributions():
    lignes = fr.bloc_attributions(referentiel_millesime="2026", prometheus=True)
    assert lignes[0] == "Détections : NASA FIRMS / LANCE / ESDIS (voir le disclaimer)"
    assert "IGN Admin Express, millésime 2026" in lignes[1]
    assert "Open-Meteo (CC BY 4.0)" in lignes[2]
    assert "Prométhée" in lignes[3]
    # sans hotspot affiché, pas de citation FIRMS.
    sans = fr.bloc_attributions(referentiel_millesime="2026", hotspots=False)
    assert not any("FIRMS" in l for l in sans)


def test_barèmes_config_coherents_avec_le_lexique():
    """Les seuils versionnés en config produisent bien 4/6/4 classes."""
    from vigifeu.model.db import load_config
    cfg = load_config("config/params.toml")["lexique"]
    assert len(cfg["dc_seuils"]) == len(fr._DC_CLASSES) - 1
    assert len(cfg["fwi_seuils"]) == len(fr._FWI_CLASSES) - 1
    assert len(cfg["sim_percentile_seuils"]) == len(fr._SIM_CLASSES) - 1
