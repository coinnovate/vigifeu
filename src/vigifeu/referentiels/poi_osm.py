"""Import OSM du référentiel POI (Spec 06 §2.2, phase 2, bloc 1, étape 2).

Consomme la sortie native de l'API Overpass (`[out:json]`, avec `out center` pour les
ways/relations) : `{elements: [{type, id, lat/lon | center, tags}, ...]}`. Chaque élément
dont les tags matchent une règle de catégorie (config `[poi].osm_rules`) devient un POI
ponctuel. Upsert idempotent par clé naturelle (`source='osm'`, `source_ref='type/id'`).

⚠️ Ne pas confondre avec `engine/overpass.py` (passages satellites) : ici « Overpass » =
l'API OpenStreetMap. Licence **ODbL → attribution obligatoire** (affichée sur le site).

Récupération de la donnée (hors code, ops) : requête Overpass sur la bbox voulue, ex.
`[out:json][timeout:60]; ( node["tourism"="camping"](bbox); way["tourism"="camping"](bbox);
… ); out center;` → enregistrer le JSON, puis `import_poi_osm`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class PoiImportError(Exception):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _point(el: dict) -> tuple[float, float] | None:
    """Point représentatif : lat/lon d'un node, ou `center` d'un way/relation (`out center`)."""
    if el.get("lat") is not None and el.get("lon") is not None:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center")
    if c and c.get("lat") is not None and c.get("lon") is not None:
        return float(c["lat"]), float(c["lon"])
    return None


def _category(tags: dict, rules: list[dict]) -> str | None:
    """Première règle dont TOUS les tags `match` sont présents et égaux."""
    for rule in rules:
        match = rule.get("match") or {}
        if match and all(tags.get(k) == v for k, v in match.items()):
            return rule["category"]
    return None


def import_poi_osm(
    conn: sqlite3.Connection,
    source: str | Path,
    config: dict,
    *,
    imported_at: str | None = None,
) -> dict:
    """Importe/actualise les POI OSM depuis un JSON Overpass (idempotent).

    Upsert par (`source`, `source_ref`) : rejouer le même export ne duplique pas. Les
    éléments sans catégorie reconnue ou sans point exploitable sont ignorés (comptés).
    Retourne un récap {upserted, skipped, by_category}.
    """
    rules = config.get("poi", {}).get("osm_rules") or []
    if not rules:
        raise PoiImportError("config [poi].osm_rules absente ou vide")
    stamp = imported_at or _now_utc()

    data = json.loads(Path(source).read_text(encoding="utf-8"))
    elements = data.get("elements", [])

    upserted = 0
    skipped = 0
    by_category: dict[str, int] = {}
    for el in elements:
        tags = el.get("tags") or {}
        category = _category(tags, rules)
        if category is None:
            skipped += 1
            continue
        pt = _point(el)
        if pt is None:
            skipped += 1  # matché mais sans géométrie (way sans `out center`)
            continue
        lat, lon = pt
        source_ref = f"{el.get('type')}/{el.get('id')}"
        conn.execute(
            "INSERT INTO poi (source, source_ref, category, nom, lat, lon, imported_at) "
            "VALUES ('osm', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, source_ref) DO UPDATE SET "
            "category=excluded.category, nom=excluded.nom, lat=excluded.lat, "
            "lon=excluded.lon, imported_at=excluded.imported_at",
            (source_ref, category, tags.get("name"), lat, lon, stamp),
        )
        upserted += 1
        by_category[category] = by_category.get(category, 0) + 1

    conn.commit()
    return {"upserted": upserted, "skipped": skipped, "by_category": by_category}
