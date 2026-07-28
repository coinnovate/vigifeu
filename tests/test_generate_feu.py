"""Rendu de la fiche feu depuis le rejeu Saumos (Spec 03 §3, Spec 04).

Rejeu incrémental hermétique (mêmes données que le jalon L3), puis on force le feu
en mode archive et on génère sa fiche. Vérifie : structure présente, phrases du
lexique bien assemblées, et **aucun terme interdit** (préfiguration du lint §9.1).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "feu-2026-saumos.html"

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.generate.carte import load_carte_context, render_carte
from vigifeu.generate.commune import load_commune_context, render_commune
from vigifeu.generate.feu import load_fire_context, render_feu
from vigifeu.generate.geojson import feu_geojson, national_geojson
from vigifeu.generate.publish import ensure_public_id
from vigifeu.generate.runner import write_carte_config
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
    assert "<link rel=\"canonical\" href=\"https://sentifeu.fr/feux/2026-saumos/\">" in page
    assert "| Sentifeu</title>" in page  # marque publique (codename interne = Vigifeu)
    assert "Chronologie" in page and "Communes concernées" in page
    assert "NASA FIRMS" in page  # attributions présentes
    # Carte = enrichissement : scripts présents mais DIFFÉRÉS (defer), contenu complet sans eux (P3/§8)
    assert 'data-carte="feu"' in page
    assert 'src="/static/js/maplibre-gl.js" defer' in page
    assert 'src="/static/js/carte.js" defer' in page
    # La clé MapTiler ne doit JAMAIS apparaître dans le HTML de la fiche (elle vit dans carte-config.js)
    assert "maptiler" not in page.lower()
    assert os.environ.get("VIGIFEU_MAPTILER_KEY", "ABSENTE") not in page


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


SAUMOS, LE_PORGE = "33503", "33333"


@pytest.fixture(scope="module")
def commune_html(saumos_archive):
    conn, config, _ = saumos_archive
    env = make_env(config["generate"]["templates_dir"])
    ctx = load_commune_context(conn, config, SAUMOS)
    return ctx, render_commune(env, ctx)


def test_commune_entete_et_situation(commune_html):
    ctx, page = commune_html
    assert ctx["nom"] == "Saumos"
    assert "Incendies à Saumos (33)" in page
    # Saumos est archivé → pas de feu actif, mais fiche complète (§4.8)
    assert ctx["aucun_feu"] is not None
    assert "Aucun incendie suivi actuellement" in page
    assert "aucun risque" not in page.lower()  # ton neutre, jamais rassurant


def test_commune_historique_liste_le_feu_suivi(commune_html):
    ctx, page = commune_html
    # le feu de Saumos (archivé) apparaît dans « feux suivis », avec lien vers sa fiche
    suivis = ctx["historique"]["suivis"]
    assert any("Saumos" in s["phrase"] for s in suivis)
    assert any(s["href"] == "/feux/2026-saumos/" for s in suivis)
    assert "Feux suivis par Sentifeu" in page
    assert "/feux/2026-saumos/" in page


def test_commune_contexte_secheresse_degrade(commune_html):
    ctx, page = commune_html
    # drought non armé → bloc dégradé (P6), pas un trou silencieux
    assert ctx["contexte"]["secheresse_indispo"] is True
    assert "momentanément indisponible" in page


def test_commune_structure_lint_et_marque(commune_html):
    ctx, page = commune_html
    assert page.startswith("<!doctype html>")
    assert "| Sentifeu</title>" in page
    assert "canonical\" href=\"https://sentifeu.fr/communes/33503-saumos/\"" in page
    bas = page.lower()
    for terme in fr.TERMES_INTERDITS:
        assert terme.lower() not in bas, f"terme interdit : {terme!r}"


def _extract_jsonld(page):
    import json
    import re
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert m, "aucun bloc JSON-LD"
    return json.loads(m.group(1))


def test_jsonld_feu_event(html):
    _, page = html
    data = _extract_jsonld(page)
    assert data["@context"] == "https://schema.org"
    types = {n["@type"] for n in data["@graph"]}
    assert {"Organization", "Event"} <= types
    event = next(n for n in data["@graph"] if n["@type"] == "Event")
    assert event["name"].startswith("Feu de Saumos")
    assert event["startDate"].startswith("2026-07-22") and event["startDate"].endswith("Z")
    assert event["about"] and all(c["@type"] == "Place" for c in event["about"])  # communes liées


def test_jsonld_commune_place(commune_html):
    _, page = commune_html
    data = _extract_jsonld(page)
    types = {n["@type"] for n in data["@graph"]}
    assert {"Organization", "AdministrativeArea"} <= types
    place = next(n for n in data["@graph"] if n["@type"] == "AdministrativeArea")
    assert place["name"] == "Saumos"
    assert "geo" in place and place["geo"]["@type"] == "GeoCoordinates"


def test_og_meta_par_departement(html, commune_html):
    _, page = html
    assert 'property="og:title"' in page
    assert 'property="og:image" content="https://sentifeu.fr/static/og/dept-33.svg"' in page
    assert 'name="twitter:card"' in page
    _, cpage = commune_html
    assert 'property="og:image" content="https://sentifeu.fr/static/og/dept-33.svg"' in cpage


def test_feu_geojson_cellules_et_enveloppe(saumos_archive):
    conn, config, saumos_id = saumos_archive
    gj = feu_geojson(conn, config, saumos_id)
    assert gj["type"] == "FeatureCollection"
    couches = {f["properties"]["couche"] for f in gj["features"]}
    assert "cellule" in couches and "enveloppe" in couches
    # une cellule porte un libellé du lexique et une géométrie polygonale
    cell = next(f for f in gj["features"] if f["properties"]["couche"] == "cellule")
    assert cell["geometry"]["type"] == "Polygon"
    assert cell["properties"]["state"] in ("front_actif", "recent", "plus_detecte")


def test_national_geojson_et_carte_page(db):
    conn, config = db
    conn.execute(
        "INSERT INTO fire_event (public_id, created_at, first_acq_at, last_acq_at, "
        "qualification, lifecycle, confidence_level) VALUES "
        "('2026-testville', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', "
        "'2026-07-02T00:00:00Z', 'vegetation_confirme', 'actif', 'confirme')"
    )
    fid = conn.execute("SELECT id FROM fire_event WHERE public_id='2026-testville'").fetchone()["id"]
    conn.execute(
        "INSERT INTO fire_event_version (fire_event_id, version_n, computed_at, geometry_wkt) "
        "VALUES (?, 1, '2026-07-02T00:00:00Z', 'POLYGON((2 46, 3 46, 3 47, 2 47, 2 46))')",
        (fid,),
    )
    conn.commit()

    gj = national_geojson(conn, config)
    assert len(gj["features"]) == 1
    props = gj["features"][0]["properties"]
    assert props["url"] == "/feux/2026-testville/" and props["nom"] == "Feu de Testville"
    assert gj["features"][0]["geometry"]["type"] == "Point"

    env = make_env(config["generate"]["templates_dir"])
    page = render_carte(env, load_carte_context(conn, config))
    assert 'data-carte="national"' in page
    assert "Feu de Testville" in page and "/feux/2026-testville/" in page
    assert page.startswith("<!doctype html>")
    assert "<title>Sentifeu — carte des incendies" in page


def test_carte_config_isole_la_cle(db, tmp_path, monkeypatch):
    conn, config = db
    cfg = {**config, "generate": {**config["generate"], "site_dir": str(tmp_path / "s")}}
    monkeypatch.setenv("VIGIFEU_MAPTILER_KEY", "CLE-SECRETE-TEST")
    path = write_carte_config(cfg)
    contenu = path.read_text(encoding="utf-8")
    assert "CLE-SECRETE-TEST" in contenu and "SENTIFEU_MAPTILER_KEY" in contenu
    # sans clé : fichier valide avec clé vide (carte se masque, alternative texte reste)
    monkeypatch.delenv("VIGIFEU_MAPTILER_KEY")
    contenu2 = write_carte_config(cfg).read_text(encoding="utf-8")
    assert '""' in contenu2


def test_runner_consomme_regen_queue(saumos_archive, tmp_path):
    """Le runner écrit la fiche feu, marque la file, et diffère commune/carte (étape C)."""
    import copy

    from vigifeu.engine.regen import enqueue
    from vigifeu.generate.runner import consume, sync_static

    conn, config, saumos_id = saumos_archive
    cfg = copy.deepcopy(config)
    cfg["generate"]["site_dir"] = str(tmp_path / "site")

    enqueue(conn, "feu", str(saumos_id), stamp="2026-07-28T00:00:00Z")
    enqueue(conn, "carte", "france", stamp="2026-07-28T00:00:00Z")
    conn.commit()

    sync_static(cfg)
    stats = consume(conn, cfg, stamp="2026-07-28T00:00:00Z")

    # la fiche feu est écrite à l'URL du public_id, avec son GeoJSON
    fiche = tmp_path / "site" / "feux" / "2026-saumos" / "index.html"
    assert fiche.exists()
    assert (fiche.parent / "feu.geojson").exists()
    assert stats["feu"] >= 1
    # la carte nationale (accueil) + le GeoJSON national sont écrits
    assert stats["carte"] == 1
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "feux.geojson").exists()
    # les assets statiques sont copiés + la config carte générée (clé depuis l'env)
    assert (tmp_path / "site" / "static" / "css" / "vigifeu.css").exists()
    assert (tmp_path / "site" / "static" / "carte-config.js").exists()
    # la page feu de Saumos est marquée traitée (plus en attente)
    reste = conn.execute(
        "SELECT COUNT(*) AS n FROM regen_queue "
        "WHERE page_type='feu' AND page_ref=? AND processed_at IS NULL",
        (str(saumos_id),),
    ).fetchone()["n"]
    assert reste == 0


def test_golden_file_saumos(html):
    """Garde-fou §9.2 : la fiche Saumos (archive) est identique au golden approuvé.

    `page = f(données)` (P1) : la fixture est gelée, la sortie est déterministe.
    Toute évolution de gabarit se relit sur ce diff. Régénérer (après revue) :
        VIGIFEU_UPDATE_GOLDEN=1 pytest tests/test_generate_feu.py::test_golden_file_saumos
    """
    _, page = html
    if os.environ.get("VIGIFEU_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(page, encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "golden file manquant — le générer avec VIGIFEU_UPDATE_GOLDEN=1"
    attendu = GOLDEN.read_text(encoding="utf-8")
    assert page == attendu, "la fiche Saumos diffère du golden (revue de gabarit requise)"
