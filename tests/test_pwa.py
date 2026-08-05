"""PWA « installable safe » : manifest, service worker prudent, page hors-ligne.

Garde-fou central (cadrage PWA) : le service worker ne doit JAMAIS mettre en cache les
pages de feux ni les GeoJSON — la fraîcheur des détections prime (P0).
"""

from __future__ import annotations

import json

from vigifeu.generate.pwa import (
    PRECACHE,
    build_pwa,
    manifest_dict,
    write_manifest,
    write_service_worker,
)
from vigifeu.generate.templating import make_env


def _config(tmp_path, with_pwa=True):
    cfg = {
        "generate": {
            "site_dir": str(tmp_path / "site"),
            "static_dir": str(tmp_path / "static"),
            "templates_dir": "templates",
            "marque": "Sentifeu",
            "base_url": "https://sentifeu.fr",
        },
    }
    if with_pwa:
        cfg["pwa"] = {
            "name": "Sentifeu — veille",
            "short_name": "Sentifeu",
            "description": "Veille satellitaire.",
            "theme_color": "#1b2a4a",
            "background_color": "#ffffff",
        }
    # Un asset shell minimal pour que l'empreinte de cache ait de quoi lire.
    css = tmp_path / "static" / "css"
    css.mkdir(parents=True)
    (css / "vigifeu.css").write_text("body{}", encoding="utf-8")
    return cfg


def test_manifest_champs_essentiels(tmp_path):
    cfg = _config(tmp_path)
    m = manifest_dict(cfg)
    assert m["display"] == "standalone"
    assert m["start_url"] == "/" and m["scope"] == "/"
    assert m["theme_color"] == "#1b2a4a"
    tailles = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= tailles
    # Icônes non « maskable » (pas de zone de sécurité) → jamais déclarées comme telles.
    assert all(i["purpose"] == "any" for i in m["icons"])


def test_manifest_ecrit_json_valide(tmp_path):
    cfg = _config(tmp_path)
    p = write_manifest(cfg)
    assert p.name == "manifest.webmanifest"
    json.loads(p.read_text(encoding="utf-8"))   # JSON bien formé


def test_service_worker_ne_cache_ni_pages_ni_geojson(tmp_path):
    """Le SW précache le shell mais laisse pages et GeoJSON au réseau (fraîcheur P0)."""
    cfg = _config(tmp_path)
    sw = write_service_worker(cfg).read_text(encoding="utf-8")
    # Précache = présentation uniquement, jamais une page /feux/ ni un .geojson.
    assert "/static/css/vigifeu.css" in PRECACHE
    assert not any(p.startswith("/feux/") or p.endswith(".geojson") for p in PRECACHE)
    # Navigation : réseau d'abord, repli page hors-ligne (pas de mise en cache de page).
    assert "req.mode === 'navigate'" in sw
    assert "/offline.html" in sw
    # maplibre-gl.js (~1 Mo) hors précache (stale-while-revalidate seulement).
    assert "maplibre-gl.js" not in sw


def test_cache_se_renomme_quand_le_shell_change(tmp_path):
    """L'empreinte du cache change avec le contenu des assets → purge auto au déploiement."""
    cfg = _config(tmp_path)
    sw1 = write_service_worker(cfg).read_text(encoding="utf-8")
    (tmp_path / "static" / "css" / "vigifeu.css").write_text("body{color:red}", encoding="utf-8")
    sw2 = write_service_worker(cfg).read_text(encoding="utf-8")
    import re
    v1 = re.search(r"sentifeu-shell-(\w+)", sw1).group(1)
    v2 = re.search(r"sentifeu-shell-(\w+)", sw2).group(1)
    assert v1 != v2


def test_build_pwa_absent_sans_section(tmp_path):
    """Sans section [pwa], aucun artefact PWA n'est émis (statique nginx ordinaire)."""
    cfg = _config(tmp_path, with_pwa=False)
    env = make_env("templates")
    assert build_pwa(cfg, env) == {"pwa": 0}
    assert not (tmp_path / "site" / "manifest.webmanifest").exists()
    assert not (tmp_path / "site" / "sw.js").exists()


def test_build_pwa_ecrit_les_trois_artefacts(tmp_path):
    cfg = _config(tmp_path)
    env = make_env("templates", pwa=cfg["pwa"])
    assert build_pwa(cfg, env) == {"pwa": 1}
    site = tmp_path / "site"
    assert (site / "manifest.webmanifest").exists()
    assert (site / "sw.js").exists()
    offline = (site / "offline.html").read_text(encoding="utf-8")
    assert "Hors ligne" in offline
    # Le lien manifest et le theme-color sont posés par le gabarit de base (global pwa).
    assert 'rel="manifest"' in offline
    assert 'name="theme-color"' in offline
