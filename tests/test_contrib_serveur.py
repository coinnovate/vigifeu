"""Test du runner WSGI du service contributif (Spec 10 §2).

On ne lie aucun socket : `create_app` et `waitress.serve` sont remplacés par des doublures
pour vérifier le câblage (host/port/threads depuis l'environnement, app servie).
"""

from __future__ import annotations

import vigifeu.contrib.serveur as serveur


def test_main_cable_host_port_threads(monkeypatch):
    appels = {}

    sentinelle = object()
    monkeypatch.setattr(serveur, "create_app", lambda: sentinelle)
    monkeypatch.setattr(serveur, "serve", lambda app, **kw: appels.update(app=app, **kw))
    monkeypatch.setenv("CONTRIB_HOST", "0.0.0.0")
    monkeypatch.setenv("CONTRIB_PORT", "9000")
    monkeypatch.setenv("CONTRIB_THREADS", "2")

    serveur.main()

    assert appels["app"] is sentinelle
    assert appels["host"] == "0.0.0.0"
    assert appels["port"] == 9000
    assert appels["threads"] == 2


def test_main_defauts(monkeypatch):
    appels = {}
    monkeypatch.setattr(serveur, "create_app", lambda: object())
    monkeypatch.setattr(serveur, "serve", lambda app, **kw: appels.update(kw))
    for v in ("CONTRIB_HOST", "CONTRIB_PORT", "CONTRIB_THREADS"):
        monkeypatch.delenv(v, raising=False)

    serveur.main()

    assert appels["host"] == "127.0.0.1"  # jamais exposé en direct (Nginx devant)
    assert appels["port"] == 8081
    assert appels["threads"] == 4
