"""Tests du client de veille presse (Spec 09, étape 2) — parsing PUR, sans réseau.

On rejoue une réponse figée de l'API (fixture calquée sur l'exemple Saumos du guide) pour
vérifier `parse_resultat` (filtrage des statuts, valeurs vides, dédup et validation des
sources) et `build_request` (corps assemblé depuis la config). Aucun appel réseau.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigifeu.ingest import bulletins
from vigifeu.model.db import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "bulletins" / "reponse_saumos.json"


@pytest.fixture()
def resultat() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def config() -> dict:
    return load_config("config/params.toml")


def test_resume_extrait(resultat):
    parsed = bulletins.parse_resultat(resultat)
    assert parsed["resume"].startswith("Le feu de Saumos a débuté")
    assert "1 750 pompiers" in parsed["resume"]


def test_indicateurs_filtres(resultat):
    """Seuls confirmé/environ porteurs d'une valeur : on garde 3 (surface, pompiers, évacués),
    on écarte `heure de début` (valeur vide) et `origine` (inconnu)."""
    parsed = bulletins.parse_resultat(resultat)
    noms = [i["indicateur"] for i in parsed["indicateurs"]]
    assert noms == ["surface brûlée", "nombre de pompiers mobilisés", "personnes évacuées"]
    statuts = {i["statut"] for i in parsed["indicateurs"]}
    assert statuts == {"confirmé", "environ"}


def test_inconnu_et_valeur_vide_ecartes(resultat):
    parsed = bulletins.parse_resultat(resultat)
    noms = [i["indicateur"] for i in parsed["indicateurs"]]
    assert "origine" not in noms          # statut inconnu
    assert "heure de début" not in noms   # valeur vide


def test_sources_dedupliquees_et_validees(resultat):
    """URLs distinctes http(s), dans l'ordre ; les doublons (franceinfo, sudouest) fusionnent,
    les entrées non-URL (« », « pas-une-url ») sont filtrées. Hôte affiché sans www."""
    parsed = bulletins.parse_resultat(resultat)
    hotes = [s["hote"] for s in parsed["sources"]]
    assert hotes == [
        "france3-regions.franceinfo.fr",
        "sudouest.fr",
        "francebleu.fr",
        "20minutes.fr",
    ]
    # Aucune source invalide n'a survécu.
    assert all(s["url"].startswith("http") for s in parsed["sources"])


def test_metadonnees_passees(resultat):
    parsed = bulletins.parse_resultat(resultat)
    assert parsed["articles_valides"] == 6
    assert parsed["fournisseurs_ia"]["resume"] == "mistral"


def test_est_vide(resultat):
    assert bulletins.est_vide(bulletins.parse_resultat(resultat)) is False
    assert bulletins.est_vide(bulletins.parse_resultat({})) is True
    # Résumé vide mais un indicateur confirmé → pas vide.
    partiel = {"indicateurs": [{"indicateur": "surface brûlée", "valeur": "10 ha", "statut": "confirmé"}]}
    assert bulletins.est_vide(bulletins.parse_resultat(partiel)) is False


def test_build_request(config):
    body = bulletins.build_request("incendie Saumos", "25/07/2026", config)
    assert body["mots_cles"] == "incendie Saumos"
    assert body["date_jour"] == "25/07/2026"
    assert body["nb_articles"] == config["bulletins"]["nb_articles"]
    assert body["min_sources"] == config["bulletins"]["min_sources"]
    # Le jeu d'indicateurs vient de la config (non vide, format {nom, type, ...}).
    assert body["indicateurs"] and body["indicateurs"][0]["nom"] == "surface brûlée"
