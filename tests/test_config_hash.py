"""Tests du hash de configuration (Spec 02 P4, §5.3).

Le hash identifie le jeu de paramètres d'interprétation d'une fiche. Il doit être
stable (mêmes valeurs ⇒ même hash), sensible aux valeurs décisionnelles, et
insensible aux sections sans effet sur l'interprétation.
"""

from __future__ import annotations

import copy

from vigifeu.model.db import config_hash, load_config


def test_hash_stable_et_court():
    config = load_config("config/params.toml")
    h1 = config_hash(config)
    h2 = config_hash(copy.deepcopy(config))
    assert h1 == h2
    assert len(h1) == 12
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_sensible_a_un_seuil_de_clustering():
    config = load_config("config/params.toml")
    before = config_hash(config)
    config["clustering"]["d_link_m"] = 2000
    assert config_hash(config) != before


def test_hash_sensible_a_la_qualification():
    config = load_config("config/params.toml")
    before = config_hash(config)
    config["qualification"]["n_franc"] = 12
    assert config_hash(config) != before


def test_hash_insensible_aux_sections_non_decisionnelles():
    """Changer un timeout FIRMS ou une URL ne requalifie rien."""
    config = load_config("config/params.toml")
    before = config_hash(config)
    config["firms"]["timeout_s"] = 999
    config["monitoring"]["gap_alert_hours"] = 48
    assert config_hash(config) == before
