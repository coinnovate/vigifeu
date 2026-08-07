"""Tests de la base contributions (Spec 10, étape 1 : squelette).

Vérifie : la migration `migrations_contrib/001` (tables `contribution` + `ip_blocklist`,
schema_version, index unique `image_sha256`, contraintes de statut), l'idempotence de la
migration, l'accès socle en LECTURE SEULE (query_only interdit l'écriture, préservant
l'invariant d'écrivain unique), et la présence de la section de config `[contributions]`.
"""

from __future__ import annotations

import sqlite3

import pytest

from vigifeu.contrib.db import connect_contrib, connect_socle_readonly, migrate_contrib
from vigifeu.model.db import connect, load_config, migrate

NOW = "2026-08-07T13:00:00Z"


@pytest.fixture()
def cconn(tmp_path):
    """Base contributions migrée (vierge)."""
    c = connect_contrib(tmp_path / "contributions.db")
    migrate_contrib(c)
    yield c
    c.close()


def _colonnes(c: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _contrib(c, sha256="a" * 64, **kw):
    champs = {
        "captured_at": NOW,
        "image_sha256": sha256,
        "consentement_at": NOW,
        "cgu_version": "2026-08-07",
        "created_at": NOW,
    }
    champs.update(kw)
    cols = ", ".join(champs)
    ph = ", ".join(f":{k}" for k in champs)
    c.execute(f"INSERT INTO contribution ({cols}) VALUES ({ph})", champs)


def test_version_schema(cconn):
    v = cconn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v == 1


def test_tables_presentes(cconn):
    tables = {r["name"] for r in cconn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"contribution", "ip_blocklist"} <= tables


def test_contribution_colonnes(cconn):
    attendu = {
        "id", "public_id", "fire_event_id", "hotspot_raw_id", "distance_km", "captured_at",
        "image_path", "thumb_path", "image_sha256", "largeur", "hauteur", "thumb_largeur",
        "thumb_hauteur", "email", "ip_hash", "consentement_at", "cgu_version", "code_insee",
        "statut", "score_nsfw", "score_feu", "auto_verdict", "auto_json", "moteur_auto",
        "moderee_par", "motif_rejet", "created_at", "moderee_at", "publiee_at",
        "purge_prevue_at", "purgee_at",
    }
    assert attendu <= _colonnes(cconn, "contribution")


def test_statut_defaut_soumise(cconn):
    _contrib(cconn)
    cconn.commit()
    assert cconn.execute("SELECT statut FROM contribution").fetchone()["statut"] == "soumise"


def test_statut_invalide_refuse(cconn):
    """La contrainte CHECK borne les statuts (machine à états §3.2)."""
    with pytest.raises(sqlite3.IntegrityError):
        _contrib(cconn, statut="n_importe_quoi")


def test_sha256_unique(cconn):
    """Anti-doublon (§3.4) : la même image (même sha256) ne se resoumet pas."""
    _contrib(cconn, sha256="b" * 64)
    cconn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _contrib(cconn, sha256="b" * 64)


def test_ip_blocklist_source_bornee(cconn):
    """`source` ne peut être que 'manuel' ou 'auto'."""
    cconn.execute(
        "INSERT INTO ip_blocklist (ip_hash, source, cree_at) VALUES ('h', 'manuel', ?)", (NOW,)
    )
    cconn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cconn.execute(
            "INSERT INTO ip_blocklist (ip_hash, source, cree_at) VALUES ('h2', 'pirate', ?)", (NOW,)
        )


def test_migration_idempotente(cconn):
    assert migrate_contrib(cconn) == []


def test_socle_lecture_seule(tmp_path):
    """La socle s'ouvre en lecture seule : lecture OK, écriture refusée (invariant écrivain unique)."""
    socle = tmp_path / "socle.db"
    c = connect(socle)
    migrate(c)
    c.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('fixture', ?, 'ok')", (NOW,)
    )
    c.commit()
    c.close()

    ro = connect_socle_readonly(socle)
    assert ro.execute("SELECT COUNT(*) AS n FROM ingestion_run").fetchone()["n"] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute(
            "INSERT INTO ingestion_run (source, started_at, status) VALUES ('x', ?, 'ok')", (NOW,)
        )
    ro.close()


def test_socle_absente_leve(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect_socle_readonly(tmp_path / "inexistante.db")


def test_config_contributions():
    """La section [contributions] existe, activated=false par défaut, db_path distinct."""
    config = load_config("config/params.toml")
    contrib = config["contributions"]
    assert contrib["activated"] is False
    assert contrib["db_path"] != config["general"]["db_path"]
    assert contrib["rayon_max_km"] == 10.0
