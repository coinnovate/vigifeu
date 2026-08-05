"""Orchestration generer_bulletins (Spec 09 §4, étape 4) — sans réseau.

On monkeypatche `bulletins.fetch_bulletin` pour rejouer succès / vide / erreur. Vérifie :
insertion d'un bulletin non vide + idempotence (rejeu = no-op), feu sans presse (0 ligne,
trace ingestion_run), erreur/timeout consigné non bloquant, feu sans commune sauté,
activated=false = marche à blanc, regen enfilée pour les feux touchés.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.ingest import bulletins

CLOCK = datetime(2026, 7, 25, 13, 0, tzinfo=UTC)  # 15h00 Europe/Paris → date_bulletin 2026-07-25

PARSED_OK = {
    "resume": "Le feu de Saumos a mobilisé au moins 1 750 pompiers.",
    "indicateurs": [{"indicateur": "surface brûlée", "valeur": "≥ 34000 ha", "statut": "confirmé"}],
    "sources": [{"url": "https://www.sudouest.fr/a", "hote": "sudouest.fr"}],
    "articles_valides": 6,
    "fournisseurs_ia": {"resume": "mistral"},
}
PARSED_VIDE = {"resume": "", "indicateurs": [], "sources": [], "articles_valides": 0,
               "fournisseurs_ia": None}


def _actif(conn, code_insee="33001", slug="saumos", nom="Saumos", *, emprise=True):
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES (?, ?, ?)", (code_insee, slug, nom))
    conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle, qualification, first_acq_at) "
        "VALUES ('2026-07-25T00:00:00Z', 'actif', 'vegetation_confirme', '2026-07-25T00:00:00Z')"
    )
    fid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    if emprise:
        conn.execute(
            "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from) "
            "VALUES (?, ?, 'emprise_dans_commune', '2026-07-25T00:00:00Z')",
            (fid, code_insee),
        )
    conn.commit()
    return fid


def _on(config):
    config["bulletins"]["activated"] = True
    return config


def _patch(monkeypatch, retour):
    monkeypatch.setattr(bulletins, "fetch_bulletin", lambda mc, dj, cfg: retour)


def test_insertion_et_idempotence(db, monkeypatch):
    conn, config = db
    fid = _actif(conn)
    _patch(monkeypatch, PARSED_OK)
    stats = bulletins.generer_bulletins(conn, _on(config), clock=CLOCK)
    assert stats["appels"] == 1 and stats["inseres"] == 1
    row = conn.execute("SELECT * FROM bulletin WHERE fire_event_id=?", (fid,)).fetchone()
    assert row["date_bulletin"] == "2026-07-25"
    assert row["mots_cles"] == "incendie Saumos"
    assert row["provider"] == "co-innovate"
    assert "1 750 pompiers" in row["resume"]
    # Regen du feu enfilée.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM regen_queue WHERE page_type='feu' AND page_ref=?", (str(fid),)
    ).fetchone()["n"] == 1
    # Rejeu le même jour = no-op (P1).
    stats2 = bulletins.generer_bulletins(conn, config, clock=CLOCK)
    assert stats2["deja_presents"] == 1 and stats2["appels"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 1


def test_feu_sans_presse_aucune_ligne(db, monkeypatch):
    conn, config = db
    _actif(conn)
    _patch(monkeypatch, PARSED_VIDE)
    stats = bulletins.generer_bulletins(conn, _on(config), clock=CLOCK)
    assert stats["vides"] == 1 and stats["inseres"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 0
    # L'absence est consignée dans ingestion_run.
    run = conn.execute("SELECT status, error_text FROM ingestion_run WHERE source='bulletins'").fetchone()
    assert run["status"] == "ok"
    assert "vides" in (run["error_text"] or "")


def test_erreur_non_bloquante(db, monkeypatch):
    conn, config = db
    _actif(conn)

    def _boom(mc, dj, cfg):
        raise bulletins.BulletinError("HTTP 429 (réessayable)")

    monkeypatch.setattr(bulletins, "fetch_bulletin", _boom)
    stats = bulletins.generer_bulletins(conn, _on(config), clock=CLOCK)
    assert stats["erreurs"] == 1 and stats["inseres"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 0


def test_feu_sans_commune_saute(db, monkeypatch):
    conn, config = db
    _actif(conn, emprise=False)  # actif mais aucune relation commune
    _patch(monkeypatch, PARSED_OK)
    stats = bulletins.generer_bulletins(conn, _on(config), clock=CLOCK)
    assert stats["actifs"] == 1 and stats["sans_commune"] == 1 and stats["appels"] == 0


def test_desactive_marche_a_blanc(db, monkeypatch):
    conn, config = db
    config["bulletins"]["activated"] = False   # explicite : marche à blanc quand désactivé
    _actif(conn)
    _patch(monkeypatch, PARSED_OK)  # ne doit pas être appelé
    stats = bulletins.generer_bulletins(conn, config, clock=CLOCK)
    assert stats["appels"] == 0 and stats["inseres"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 0


def test_generer_pour_feu_cible(db, monkeypatch):
    """Chemin ciblé (CLI/démo) : un feu NON actif reçoit quand même un bulletin, avec date visée."""
    conn, config = db
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33001', 'saumos', 'Saumos')")
    conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle, qualification, first_acq_at) "
        "VALUES ('2026-07-25T00:00:00Z', 'archive', 'vegetation_confirme', '2026-07-25T00:00:00Z')"
    )
    fid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from) "
        "VALUES (?, '33001', 'emprise_dans_commune', '2026-07-25T00:00:00Z')", (fid,)
    )
    conn.commit()
    _patch(monkeypatch, PARSED_OK)
    res = bulletins.generer_pour_feu(conn, config, fid, date_jour="25/07/2026")
    assert res["status"] == "insere"
    assert res["date_bulletin"] == "2026-07-25"
    row = conn.execute("SELECT date_bulletin, mots_cles FROM bulletin WHERE fire_event_id=?", (fid,)).fetchone()
    assert row["date_bulletin"] == "2026-07-25" and row["mots_cles"] == "incendie Saumos"
    # Rejeu = déjà présent (idempotent).
    assert bulletins.generer_pour_feu(conn, config, fid, date_jour="25/07/2026")["status"] == "deja_present"


def test_cap_max_feux(db, monkeypatch):
    conn, config = db
    for i in range(3):
        _actif(conn, code_insee=f"3300{i}", slug=f"c{i}", nom=f"C{i}")
    _patch(monkeypatch, PARSED_OK)
    config = _on(config)
    config["bulletins"]["max_feux_par_jour"] = 2
    stats = bulletins.generer_bulletins(conn, config, clock=CLOCK)
    assert stats["actifs"] == 3 and stats["appels"] == 2 and stats["non_traites"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 2
