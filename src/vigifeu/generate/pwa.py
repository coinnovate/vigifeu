"""PWA « installable safe » : manifest + service worker + page hors-ligne (cadrage PWA).

Niveau volontairement prudent. Le service worker ne met **jamais** en cache les pages
de feux ni les GeoJSON : la fraîcheur des détections prime (Spec 04 §9.5, P0). Il ne
précache que le « shell » de présentation (CSS, JS carte, icônes, page hors-ligne) et
sert tout le reste directement depuis le réseau. But : rendre le site installable sur
l'écran d'accueil et charger le shell instantanément, sans jamais afficher un feu périmé.

Artefacts « site-level » (comme sitemaps/robots) écrits en fin de build par `finalize_site`.
Le `sw.js` vit à la racine (et non sous /static/) pour contrôler tout le site : la portée
d'un service worker ne peut pas dépasser le répertoire d'où il est servi.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jinja2 import Environment

from vigifeu.generate.writer import write_atomic

# Assets « shell » précachés à l'installation — présentation uniquement, jamais de donnée
# feu. Chemins publics (servis sous la racine). maplibre-gl.js (~1 Mo) est volontairement
# HORS précache : trop lourd pour l'installation ; il est mis en cache opportunistement par
# la stratégie stale-while-revalidate appliquée à tout /static/.
SHELL_STATIC = [
    "/static/css/vigifeu.css",
    "/static/css/maplibre-gl.css",
    "/static/js/carte.js",
    "/static/favicon.ico",
    "/static/favicon-192.png",
    "/static/favicon-512.png",
    "/static/apple-touch-icon.png",
    "/static/img/logo.png",
]
PRECACHE = SHELL_STATIC + ["/offline.html", "/manifest.webmanifest"]


def manifest_dict(config: dict) -> dict:
    """Dictionnaire du Web App Manifest, dérivé de la config (aucune constante magique)."""
    gen, pwa = config["generate"], config.get("pwa", {})
    return {
        "name": pwa.get("name", gen["marque"]),
        "short_name": pwa.get("short_name", gen["marque"]),
        "description": pwa.get("description", ""),
        "lang": "fr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": pwa.get("background_color", "#ffffff"),
        "theme_color": pwa.get("theme_color", "#ffffff"),
        # Icônes 192/512 déjà présentes (favicon Sentifeu). `purpose: any` : nos icônes ne
        # sont pas dessinées « maskable » (pas de zone de sécurité), on ne les déclare donc
        # pas comme telles pour éviter un rognage sur Android.
        "icons": [
            {"src": "/static/favicon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/favicon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    }


def write_manifest(config: dict) -> Path:
    site = Path(config["generate"]["site_dir"])
    body = json.dumps(manifest_dict(config), ensure_ascii=False, indent=2) + "\n"
    return write_atomic(site / "manifest.webmanifest", body)


def _shell_version(config: dict) -> str:
    """Empreinte courte du contenu des assets shell → nom de cache auto-busté.

    Le cache se renomme dès qu'un asset de présentation change (nouveau CSS, JS carte) :
    l'ancien cache est purgé à l'activation, sans version à incrémenter à la main.
    """
    src = Path(config["generate"]["static_dir"])
    h = hashlib.sha1()
    for pub in SHELL_STATIC:
        f = src / pub[len("/static/"):]
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def write_service_worker(config: dict) -> Path:
    site = Path(config["generate"]["site_dir"])
    cache = f"sentifeu-shell-{_shell_version(config)}"
    sw = f"""/* Service worker Sentifeu — GÉNÉRÉ (generate/pwa.py). Ne pas éditer à la main.
   Stratégie « safe » : précache le shell de présentation, ne met JAMAIS en cache les
   pages de feux ni les GeoJSON (fraîcheur des détections = P0). */
const CACHE = {json.dumps(cache)};
const PRECACHE = {json.dumps(PRECACHE)};

self.addEventListener('install', (e) => {{
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
}});

self.addEventListener('activate', (e) => {{
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
}});

self.addEventListener('fetch', (e) => {{
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // tuiles/API tierces : jamais interceptées

  // Présentation : stale-while-revalidate (aucune donnée feu ici).
  if (url.pathname.startsWith('/static/')) {{
    e.respondWith(caches.open(CACHE).then(async (cache) => {{
      const cached = await cache.match(req);
      const network = fetch(req).then((res) => {{
        if (res && res.ok) cache.put(req, res.clone());
        return res;
      }}).catch(() => cached);
      return cached || network;
    }}));
    return;
  }}

  // Pages HTML : réseau d'abord, repli page hors-ligne. On ne met JAMAIS une page en
  // cache : une fiche feu périmée ne doit jamais être servie.
  if (req.mode === 'navigate') {{
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
    return;
  }}
  // Reste (*.geojson, feux.xml, etc.) : réseau direct, pas d'interception.
}});
"""
    return write_atomic(site / "sw.js", sw)


def write_offline_page(config: dict, env: Environment) -> Path:
    """Page servie par le service worker quand une navigation échoue hors ligne."""
    site = Path(config["generate"]["site_dir"])
    gen = config["generate"]
    ctx = {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": "/offline.html",
        "page_title": f"Hors ligne | {gen['marque']}",
        "page_description": "Connexion indisponible.",
        "og_image": None,
        "jsonld": None,
        "fil_ariane": [{"label": "Accueil", "href": "/"}, {"label": "Hors ligne", "href": None}],
        "latence_texte": None,
        "attributions": [],
    }
    return write_atomic(site / "offline.html", env.get_template("offline.html.j2").render(**ctx))


def build_pwa(config: dict, env: Environment) -> dict:
    """Écrit manifest + service worker + page hors-ligne. Retourne un décompte."""
    if not config.get("pwa"):
        return {"pwa": 0}   # section absente = pas de PWA (site statique nginx ordinaire)
    write_manifest(config)
    write_service_worker(config)
    write_offline_page(config, env)
    return {"pwa": 1}
