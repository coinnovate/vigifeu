"""Rendu de la fiche feu depuis le rejeu Saumos (Spec 03 §3, Spec 04).

Rejeu incrémental hermétique (mêmes données que le jalon L3), puis on force le feu
en mode archive et on génère sa fiche. Vérifie : structure présente, phrases du
lexique bien assemblées, et **aucun terme interdit** (préfiguration du lint §9.1).
"""

from __future__ import annotations

import pytest

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.generate.feu import load_fire_context, render_feu
from vigifeu.generate.publish import ensure_public_id
from vigifeu.generate.templating import make_env
from vigifeu.lexique import fr
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources
from vigifeu.referentiels.communes import import_communes

from .conftest import load_saumos_hotspots

BBOX = (44.5, 45.3, -1.30, -0.30)
COMMUNES = "tests/fixtures/communes/gironde-ouest.geojson"
DAYS = [f"2026-07-{d:02d}" for d in range(20, 28)]


@pytest.fixture(scope="module")
def saumos_archive():
    """Rejeu Saumos complet, feu forcé en mode archive (fiche de référence)."""
    conn = connect(":memory:")
    migrate(conn)
    config = load_config("config/params.toml")
    sync_satellite_sources(conn, config)
    import_communes(conn, COMMUNES, millesime="test-gironde")
    invalidate_commune_index(conn)
    for d in DAYS:
        load_saumos_hotspots(conn, day_prefix=d, bbox=BBOX)
        build_overpasses(conn, config)
        process_cycle(conn, config, stamp=d + "T23:59:00Z")
    saumos_id = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at='2026-07-22T11:55:00Z' "
        "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99 LIMIT 1"
    ).fetchone()["fire_event_id"]
    ensure_public_id(conn, saumos_id)
    conn.execute("UPDATE fire_event SET lifecycle='archive' WHERE id=?", (saumos_id,))
    conn.commit()
    yield conn, config, saumos_id
    conn.close()


@pytest.fixture(scope="module")
def html(saumos_archive):
    conn, config, saumos_id = saumos_archive
    ctx = load_fire_context(conn, config, saumos_id)
    env = make_env(config["generate"]["templates_dir"])
    return load_fire_context(conn, config, saumos_id), render_feu(env, ctx)


def test_contexte_entete(saumos_archive):
    conn, config, saumos_id = saumos_archive
    ctx = load_fire_context(conn, config, saumos_id)
    assert ctx["lieu"] == "Saumos"
    assert ctx["nom"].startswith("Feu de Saumos")
    assert ctx["badge_cycle"]["classe"] == "archive"
    assert ctx["bandeau_archive"].startswith("Feu archivé — dernière détection le")
    # première détection contractuelle (jalon L2/L3)
    assert ctx["first_acq"] == "22/07/2026 à 11:55 UTC"


def test_synthese_citable(saumos_archive):
    conn, config, saumos_id = saumos_archive
    ctx = load_fire_context(conn, config, saumos_id)
    assert 1 <= len(ctx["synthese"]) <= 6
    joined = " ".join(ctx["synthese"])
    assert "Feu suivi du 22/07/2026" in joined
    # une phrase de progression vers le nord doit apparaître (front ~nord, §10.1)
    assert "progressé d'environ" in joined and "nord" in joined


def test_communes_groupees(saumos_archive):
    conn, config, saumos_id = saumos_archive
    ctx = load_fire_context(conn, config, saumos_id)
    titres = [g["titre"] for g in ctx["communes_groupes"]]
    assert "Emprise sur la commune" in titres
    emprise = next(g for g in ctx["communes_groupes"] if g["titre"] == "Emprise sur la commune")
    noms = {i["nom"] for i in emprise["communes"]}
    assert "Saumos" in noms
    # chaque item pointe vers une fiche commune
    assert all(i["href"].startswith("/communes/") for i in emprise["communes"])


def test_html_structure_et_sans_js(html):
    _, page = html
    assert page.startswith("<!doctype html>")
    assert '<html lang="fr">' in page
    assert "<link rel=\"canonical\" href=\"https://vigifeu.fr/feux/" in page
    assert "Chronologie" in page and "Communes concernées" in page
    assert "NASA FIRMS" in page  # attributions présentes
    assert "<script" not in page.lower()  # contenu complet sans JS (P3)


def test_lint_lexique_aucun_terme_interdit(html):
    """Préfiguration du garde-fou §9.1 : aucun terme proscrit dans le HTML généré."""
    _, page = html
    bas = page.lower()
    for terme in fr.TERMES_INTERDITS:
        assert terme.lower() not in bas, f"terme interdit dans le HTML : {terme!r}"
    # quelques pièges opérationnels explicites
    for piege in ("éteint", "maîtrisé", "sera touché", "propagation estimée"):
        assert piege not in bas


def test_aucun_horodatage_de_generation(html):
    """Garde-fou §9.5 : seule l'heure de la donnée apparaît, jamais l'heure de génération."""
    _, page = html
    # toutes les heures affichées portent « UTC » (données) ; pas de fuseau local ni de « généré le »
    assert "généré" not in page.lower()
    assert "generated" not in page.lower()
