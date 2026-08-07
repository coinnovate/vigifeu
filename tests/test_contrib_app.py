"""Tests du squelette Flask du canal contributif (Spec 10, étape 1b).

Endpoint de santé, préfixe same-origin `/api/contrib`, migration de la base contributions
au démarrage, et report `socle_reachable` selon que la socle (lue en read-only) existe.
"""

from __future__ import annotations

import copy

import pytest

from vigifeu.contrib.app import create_app
from vigifeu.model.db import connect, load_config, migrate


@pytest.fixture()
def app_config(tmp_path):
    """Config réelle avec des db_path redirigés vers le tmp du test."""
    config = copy.deepcopy(load_config("config/params.toml"))
    config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    config["general"]["db_path"] = str(tmp_path / "socle.db")
    return config


@pytest.fixture()
def client(app_config):
    app = create_app(app_config)
    app.testing = True
    return app.test_client()


def test_health_ok_sans_socle(client):
    """Santé 200 même socle absente ; la base contributions est migrée (schema_version=1)."""
    r = client.get("/api/contrib/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["activated"] is False
    assert data["schema_version"] == 1
    assert data["socle_reachable"] is False


def test_health_socle_joignable(app_config):
    """Avec une socle migrée, socle_reachable=true (lecture seule)."""
    c = connect(app_config["general"]["db_path"])
    migrate(c)
    c.close()
    app = create_app(app_config)
    app.testing = True
    assert app.test_client().get("/api/contrib/health").get_json()["socle_reachable"] is True


def test_route_inconnue_404(client):
    assert client.get("/api/contrib/inexistant").status_code == 404


def test_prefixe_same_origin(client):
    """L'endpoint n'existe que sous /api/contrib (même origine, §2) — pas à la racine."""
    assert client.get("/health").status_code == 404
