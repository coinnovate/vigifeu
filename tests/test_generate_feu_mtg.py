"""Enrichissement MTG de la fiche feu (Spec 07 §7, étape 7).

Feu minimal + détections MTG rattachées → frise de tendance relative + fait de fraîcheur +
attribution EUMETSAT. Couvre : hausse/baisse, dégradé (trop peu de slots), section absente
(aucune détection), et l'absence de FRP MW comparable à VIIRS (tendance relative seulement).
"""

from __future__ import annotations

import pytest

from vigifeu.generate.feu import load_fire_context, render_feu
from vigifeu.generate.templating import make_env
from vigifeu.model.db import connect, load_config, migrate


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    return load_config("config/params.toml")


@pytest.fixture()
def env(config):
    return make_env(config["generate"]["templates_dir"])


def _feu(conn):
    return conn.execute(
        "INSERT INTO fire_event (public_id, created_at, first_acq_at, last_acq_at, lifecycle, "
        "confidence_level) VALUES ('2026-testmtg', '2026-08-06T10:00:00Z', '2026-08-06T10:00:00Z', "
        "'2026-08-06T12:00:00Z', 'actif', 'confirme')"
    ).lastrowid


def _det(conn, fid, acq_at, frp, *, lon=-1.0):
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682','2026-08-06T12:00:00Z')"
    ).lastrowid
    conn.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, ingestion_run_id, "
        "frp_mw, confirmed_by_fire_event_id) VALUES ('mtg-fci-fir', 44.7, ?, ?, ?, ?, ?, ?)",
        (lon, acq_at, "2026-08-06T12:00:05Z", run, frp, fid),
    )
    conn.commit()


def test_frise_tendance_hausse(conn, config, env):
    fid = _feu(conn)
    for i, frp in enumerate([10.0, 20.0, 40.0, 80.0]):        # intensité croissante
        _det(conn, fid, f"2026-08-06T11:{10 + i * 10:02d}:00Z", frp)
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"] is not None
    assert "en hausse" in ctx["mtg"]["tendance"]
    assert len(ctx["mtg"]["bars"]) == 4
    html = render_feu(env, ctx)
    assert "Évolution vue par le satellite géostationnaire" in html
    assert "première vue le" in html
    assert "EUMETSAT" in html
    assert "frise-mtg" in html


def test_tendance_baisse(conn, config, env):
    fid = _feu(conn)
    for i, frp in enumerate([80.0, 40.0, 20.0, 10.0]):
        _det(conn, fid, f"2026-08-06T11:{10 + i * 10:02d}:00Z", frp)
    ctx = load_fire_context(conn, config, fid)
    assert "en baisse" in ctx["mtg"]["tendance"]


def test_degrade_trop_peu_de_slots(conn, config, env):
    fid = _feu(conn)
    _det(conn, fid, "2026-08-06T11:10:00Z", 10.0)
    _det(conn, fid, "2026-08-06T11:20:00Z", 20.0)              # 2 slots < trend_min_points=3
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"]["tendance"] is None
    assert ctx["mtg"]["degrade"]
    html = render_feu(env, ctx)
    assert "Pas encore assez de vues" in html
    assert "frise-mtg" not in html                            # pas de frise en dégradé


def test_section_absente_sans_detection(conn, config, env):
    fid = _feu(conn)
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"] is None
    html = render_feu(env, ctx)
    assert "satellite géostationnaire" not in html


def test_intensite_par_slot_somme(conn, config, env):
    """Plusieurs pixels d'un même slot → somme (puissance totale à l'instant), pas doublon de slot."""
    fid = _feu(conn)
    _det(conn, fid, "2026-08-06T11:10:00Z", 5.0)
    _det(conn, fid, "2026-08-06T11:10:00Z", 5.0, lon=-1.02)    # même slot, autre pixel
    _det(conn, fid, "2026-08-06T11:20:00Z", 30.0)
    _det(conn, fid, "2026-08-06T11:30:00Z", 60.0)
    ctx = load_fire_context(conn, config, fid)
    # 3 slots distincts (le doublon d'instant est sommé), tendance calculable
    assert len(ctx["mtg"]["bars"]) == 3
    assert "en hausse" in ctx["mtg"]["tendance"]
