"""Tests du fetcher VigiEau (Spec 01 §3.6). HTTP mocké au format supposé de l'API."""

from __future__ import annotations

import pytest

from vigifeu.ingest import vigieau


@pytest.fixture()
def commune(db):
    conn, config = db
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33333','le-porge','Le Porge')")
    conn.commit()
    return conn, config


ZONES = [
    {
        "type": "SOU",
        "niveauGravite": "alerte",
        "arrete": {"numeroArrete": "AP-2026-77", "dateDebutValidite": "2026-07-15", "dateFinValidite": None},
    },
    {
        "type": "SUP",
        "niveauGravite": "alerte_renforcee",  # plus sévère → doit gagner
        "arrete": {"numeroArrete": "AP-2026-90", "dateDebutValidite": "2026-07-20", "dateFinValidite": "2026-09-30"},
    },
]


def test_retient_la_plus_severe(commune, monkeypatch):
    conn, config = commune
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: ZONES)
    r = vigieau.fetch_vigieau(conn, config, "33333")
    assert r["status"] == "ok" and r["inserted"] == 1
    row = conn.execute("SELECT * FROM vigieau_arrete").fetchone()
    assert row["niveau"] == "alerte_renforcee"
    assert row["arrete_ref"] == "AP-2026-90"
    assert row["date_debut"] == "2026-07-20"
    assert row["fetched_at"].endswith("Z")


def test_aucune_restriction(commune, monkeypatch):
    conn, config = commune
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: [])
    r = vigieau.fetch_vigieau(conn, config, "33333")
    assert r["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM vigieau_arrete").fetchone()["n"] == 0


def test_anti_doublon(commune, monkeypatch):
    """Une observation identique à la précédente n'est pas réinsérée (P1 : que du neuf)."""
    conn, config = commune
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: ZONES)
    assert vigieau.fetch_vigieau(conn, config, "33333")["inserted"] == 1
    assert vigieau.fetch_vigieau(conn, config, "33333")["inserted"] == 0  # inchangé
    assert conn.execute("SELECT COUNT(*) AS n FROM vigieau_arrete").fetchone()["n"] == 1


def test_changement_de_niveau_insere(commune, monkeypatch):
    conn, config = commune
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: ZONES)
    vigieau.fetch_vigieau(conn, config, "33333")
    # La situation s'aggrave : crise.
    crise = [{"niveauGravite": "crise", "arrete": {"numeroArrete": "AP-2026-99", "dateDebutValidite": "2026-08-01"}}]
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: crise)
    r = vigieau.fetch_vigieau(conn, config, "33333")
    assert r["inserted"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM vigieau_arrete").fetchone()["n"] == 2


def test_reponse_objet_englobant(commune, monkeypatch):
    """Tolère une réponse {zones:[...]} au lieu d'une liste nue."""
    conn, config = commune
    monkeypatch.setattr(vigieau, "_fetch_json", lambda *a, **k: {"zones": ZONES})
    assert vigieau.fetch_vigieau(conn, config, "33333")["inserted"] == 1


def test_niveau_inconnu_ignore(commune, monkeypatch):
    conn, config = commune
    monkeypatch.setattr(
        vigieau, "_fetch_json",
        lambda *a, **k: [{"niveauGravite": "pas_de_restriction", "arrete": {}}],
    )
    r = vigieau.fetch_vigieau(conn, config, "33333")
    assert r["inserted"] == 0


def test_panne_ne_bloque_pas(commune, monkeypatch):
    conn, config = commune

    def boom(*a, **k):
        raise vigieau.VigieauError("HTTP 503 (réessayable)")

    monkeypatch.setattr(vigieau, "_fetch_json", boom)
    r = vigieau.fetch_vigieau(conn, config, "33333")
    assert r["status"] == "error" and "503" in r["error"]
