"""Garde-fous CI du générateur (Spec 04 §9) — les 5 vérifications sur un site COMPLET.

On génère le site entier depuis le rejeu Saumos (feux actifs, communes, carte, pages,
sitemaps, flux), puis on applique : (1) lint lexique, (2) golden file [test_generate_feu],
(3) JSON-LD valide, (4) budget perf, (5) aucun horodatage de génération.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.generate.lint import lint_lexique, no_generation_timestamp
from vigifeu.generate.publish import ensure_public_id
from vigifeu.generate.runner import consume, finalize_site, sync_static
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources
from vigifeu.referentiels.communes import import_communes

from .conftest import load_saumos_hotspots

BBOX = (44.5, 45.3, -1.30, -0.30)
COMMUNES = "tests/fixtures/communes/gironde-ouest.geojson"
DAYS = [f"2026-07-{d:02d}" for d in range(20, 28)]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """Site complet généré depuis le rejeu (feux actifs → carte + Atom peuplés)."""
    out = tmp_path_factory.mktemp("site")
    conn = connect(":memory:")
    migrate(conn)
    config = load_config("config/params.toml")
    config["generate"]["site_dir"] = str(out)
    sync_satellite_sources(conn, config)
    import_communes(conn, COMMUNES, millesime="test-gironde")
    invalidate_commune_index(conn)
    for d in DAYS:
        load_saumos_hotspots(conn, day_prefix=d, bbox=BBOX)
        build_overpasses(conn, config)
        process_cycle(conn, config, stamp=d + "T23:59:00Z")
    for row in conn.execute("SELECT id FROM fire_event WHERE qualification='vegetation_confirme'"):
        ensure_public_id(conn, row["id"])
    sync_static(config)
    consume(conn, config, stamp="2026-07-28T00:00:00Z")
    finalize_site(conn, config)
    yield out
    conn.close()


def _html_files(site):
    return list(Path(site).rglob("*.html"))


def test_garde_fou_1_lint_lexique(site):
    """§9.1 — aucun terme interdit dans le HTML généré (méthodologie exclue, glossaire)."""
    violations = lint_lexique(site)
    assert violations == [], f"termes interdits : {violations}"


def test_garde_fou_3_jsonld_valide(site):
    """§9.3 — le JSON-LD de chaque page échantillon parse et porte @context/@graph."""
    echantillon = ["index.html", "feux/2026-saumos/index.html", "methodologie/index.html"]
    for rel in echantillon:
        page = (Path(site) / rel).read_text(encoding="utf-8")
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        assert m, f"JSON-LD absent de {rel}"
        data = json.loads(m.group(1))
        assert data["@context"] == "https://schema.org" and data["@graph"]


def test_garde_fou_4_budget_perf(site):
    """§5/§9.4 — HTML+CSS < 100 ko par fiche (hors carte) et aucun script bloquant."""
    css = (Path(site) / "static" / "css" / "vigifeu.css").stat().st_size
    fiches = [p for p in _html_files(site) if p.parent.name.startswith(("2026-", "33"))
              or p.parent.parent.name in ("feux", "communes")]
    assert fiches, "des fiches sont attendues"
    for p in fiches:
        taille = p.stat().st_size + css
        assert taille < 100 * 1024, f"{p} pèse {taille} o (budget 100 ko HTML+CSS)"
        html = p.read_text(encoding="utf-8")
        # tout script externe est différé (pas de JS bloquant) ; le JSON-LD inline est de la donnée
        for tag in re.findall(r"<script\b[^>]*>", html):
            if "src=" in tag:
                assert "defer" in tag or "async" in tag, f"script bloquant dans {p} : {tag}"


def test_garde_fou_5_aucun_horodatage_generation(site):
    """§9.5 — aucune heure de génération ; seule l'heure de la donnée (UTC) apparaît."""
    assert no_generation_timestamp(site) == []


def test_pages_departements_generees(site):
    """Les pages liste départements existent (fil d'Ariane commune → dept, pas de 404)."""
    dept33 = Path(site) / "departements" / "33" / "index.html"
    assert dept33.exists()
    page = dept33.read_text(encoding="utf-8")
    assert "Incendies en Gironde" in page              # nom du département (SEO), pas « département 33 »
    assert "/communes/33503-saumos/" in page          # commune du périmètre
    assert "/feux/2026-saumos/" in page                # feu suivi du département
    # index départements généré + index ET page dept au sitemap
    assert (Path(site) / "departements" / "index.html").exists()
    smp = (Path(site) / "sitemap-pages.xml").read_text(encoding="utf-8")
    assert "/departements/</loc>" in smp        # l'index /departements/
    assert "/departements/33/" in smp           # la page du département


def test_maplibre_exclu_du_budget_mais_present(site):
    """La lib carte est vendorisée (hors budget fiche) mais bien servie."""
    assert (Path(site) / "static" / "js" / "maplibre-gl.js").exists()
    assert (Path(site) / "static" / "carte-config.js").exists()
