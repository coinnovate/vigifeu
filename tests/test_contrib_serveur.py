"""Test du runner WSGI du service contributif (Spec 10 §2).

On ne lie aucun socket : `create_app` et `waitress.serve` sont remplacés par des doublures
pour vérifier le câblage (host/port/threads depuis l'environnement, app servie).
"""

from __future__ import annotations

import vigifeu.contrib.serveur as serveur


class _FakeApp:
    """App minimale : porte la config comme le vrai `create_app`."""

    config = {"VIGIFEU": {"contributions": {}}}


class _FakePlan:
    def __init__(self):
        self.demarre = self.arrete = False

    def start(self):
        self.demarre = True

    def shutdown(self, wait=True):
        self.arrete = True


def _patch(monkeypatch, appels, plan):
    monkeypatch.setattr(serveur, "create_app", lambda: _FakeApp())
    monkeypatch.setattr(serveur, "construire_planificateur", lambda cfg: plan)
    monkeypatch.setattr(serveur, "serve", lambda app, **kw: appels.update(app=app, **kw))


def test_main_cable_host_port_threads(monkeypatch):
    appels, plan = {}, _FakePlan()
    _patch(monkeypatch, appels, plan)
    monkeypatch.setenv("CONTRIB_HOST", "0.0.0.0")
    monkeypatch.setenv("CONTRIB_PORT", "9000")
    monkeypatch.setenv("CONTRIB_THREADS", "2")

    serveur.main()

    assert isinstance(appels["app"], _FakeApp)
    assert appels["host"] == "0.0.0.0" and appels["port"] == 9000 and appels["threads"] == 2
    # Planificateur démarré puis arrêté proprement autour de serve().
    assert plan.demarre and plan.arrete


def test_main_defauts(monkeypatch):
    appels, plan = {}, _FakePlan()
    _patch(monkeypatch, appels, plan)
    for v in ("CONTRIB_HOST", "CONTRIB_PORT", "CONTRIB_THREADS"):
        monkeypatch.delenv(v, raising=False)

    serveur.main()

    assert appels["host"] == "127.0.0.1"  # jamais exposé en direct (Nginx devant)
    assert appels["port"] == 8081
    assert appels["threads"] == 4
