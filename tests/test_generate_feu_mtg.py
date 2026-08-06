"""Enrichissement MTG de la fiche feu (Spec 07 §7, étape 7, révisé prod : pas de FRP).

Le 0682 est de la détection seule → la frise porte sur le NOMBRE DE PIXELS feu MTG par slot
(proxy d'étendue). Couvre : expansion / repli, dégradé (trop peu de slots), section absente.
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


def _slot(conn, fid, acq_at, n_pixels):
    """Insère `n_pixels` détections MTG rattachées au feu pour un même instant (pixels distincts)."""
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682','2026-08-06T12:00:00Z')"
    ).lastrowid
    for i in range(n_pixels):
        conn.execute(
            "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, "
            "ingestion_run_id, confidence, confirmed_by_fire_event_id) "
            "VALUES ('mtg-fci-fir', 44.7, ?, ?, ?, ?, '3', ?)",
            (-1.0 - i * 0.02, acq_at, "2026-08-06T12:00:05Z", run, fid),
        )
    conn.commit()


def test_frise_expansion(conn, config, env):
    fid = _feu(conn)
    for i, n in enumerate([1, 2, 4]):                     # de plus en plus de pixels → expansion
        _slot(conn, fid, f"2026-08-06T11:{10 + i * 10:02d}:00Z", n)
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"] is not None
    assert "en expansion" in ctx["mtg"]["tendance"]
    assert len(ctx["mtg"]["bars"]) == 3
    html = render_feu(env, ctx)
    assert "Évolution vue par le satellite géostationnaire" in html
    assert "première vue le" in html
    assert "EUMETSAT" in html
    assert "frise-mtg" in html


def test_frise_repli(conn, config, env):
    fid = _feu(conn)
    for i, n in enumerate([5, 2, 1]):                     # de moins en moins → repli
        _slot(conn, fid, f"2026-08-06T11:{10 + i * 10:02d}:00Z", n)
    ctx = load_fire_context(conn, config, fid)
    assert "en repli" in ctx["mtg"]["tendance"]


def test_degrade_trop_peu_de_slots(conn, config, env):
    fid = _feu(conn)
    _slot(conn, fid, "2026-08-06T11:10:00Z", 3)
    _slot(conn, fid, "2026-08-06T11:20:00Z", 3)           # 2 slots < trend_min_points=3
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"]["tendance"] is None
    assert ctx["mtg"]["degrade"]
    html = render_feu(env, ctx)
    assert "Pas encore assez de vues" in html
    assert "frise-mtg" not in html


def test_section_absente_sans_detection(conn, config, env):
    fid = _feu(conn)
    ctx = load_fire_context(conn, config, fid)
    assert ctx["mtg"] is None
    html = render_feu(env, ctx)
    assert "satellite géostationnaire" not in html
