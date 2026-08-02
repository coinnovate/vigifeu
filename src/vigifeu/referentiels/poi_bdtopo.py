"""Import BD TOPO du référentiel POI (Spec 06 §2.2, phase 2, bloc 1, étape 8).

Deuxième source du référentiel POI, après OSM (`poi_osm.py`). BD TOPO (IGN) donne des
catégories **officielles et fraîches** (Etalab). ⚠️ VÉRIFIÉ live (2026-08-01) : BD TOPO **V3**
n'a PAS de couches séparées santé/enseignement — les POI d'enjeu vivent dans une **unique
couche `zone_d_activite_ou_d_interet`** (les PAI), catégorisée par l'attribut `nature`
(« Camping », « Hôpital », « Enseignement primaire », « Maison de retraite »…). Champs utiles :
`cleabs` (clé), `nature`, `toponyme` (nom), géométrie surfacique (on prend le centroïde).

Deux formats, une normalisation — **exactement le pattern de `communes.py`** :

- **GeoJSON** (voie recommandée) — WFS Géoplateforme
  (`data.geopf.fr/wfs/ows`, `TYPENAMES=BDTOPO_V3:zone_d_activite_ou_d_interet`,
  `OUTPUTFORMAT=application/json&SRSNAME=CRS:84`) : léger, ciblé sur la seule couche utile,
  déjà en WGS84. Sert aussi de fixture de test.
- **GeoPackage** (`data.geopf.fr/telechargement/resource/BDTOPO`, comme Admin Express) —
  SQLite lu en pur Python, géométries décodées via le GPB de `communes.py` (réutilisé),
  Lambert-93 reprojeté WGS84. ⚠️ balaie TOUTES les couches géométriques : sur un GPKG BD TOPO
  complet (bâti = millions d'objets) c'est lent — préférer un GPKG déjà filtré, ou le WFS.

Catégorisation par règles config `[poi].bdtopo_rules` (attribut `nature` → catégorie ;
valeurs réelles vérifiées). Upsert idempotent par (`source='bdtopo'`, `source_ref=cleabs`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from vigifeu.engine import geo

# Réutilise le décodage GeoPackage Binary de l'import commune (même machinerie GPKG,
# Lot 3) plutôt que de le redéfinir — cohérent avec « réutilise la machinerie » (Spec 06).
from vigifeu.referentiels.communes import _decode_gpb


class PoiBdtopoImportError(Exception):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(attrs: dict, *keys: str):
    """Lecture tolérante (BD TOPO GPKG : casse d'attribut variable selon export)."""
    lowered = {k.lower(): v for k, v in attrs.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def _category(attrs: dict, rules: list[dict]) -> str | None:
    """Première règle dont TOUS les tags `match` sont présents (comparaison insensible
    à la casse : les valeurs `nature` BD TOPO portent accents/majuscules)."""
    for rule in rules:
        match = rule.get("match") or {}
        if not match:
            continue
        ok = True
        for k, v in match.items():
            got = _get(attrs, k)
            if got is None or str(got).strip().lower() != str(v).strip().lower():
                ok = False
                break
        if ok:
            return rule["category"]
    return None


# --- lecture des sources (GeoJSON fixture / GeoPackage production) ---

def _read_geojson(path: Path) -> Iterator[tuple[dict, BaseGeometry, bool]]:
    fc = json.loads(path.read_text(encoding="utf-8"))
    for feat in fc.get("features", []):
        g = feat.get("geometry")
        if not g:
            continue
        yield feat.get("properties", {}), shape(g), False  # GeoJSON = WGS84


def _read_geopackage(path: Path) -> Iterator[tuple[dict, BaseGeometry, bool]]:
    # BD TOPO « Services et activités » : plusieurs couches géométriques (santé, enseignement,
    # PAI). On les balaie toutes et on catégorise par attribut `nature` — pas besoin de
    # connaître les noms de couche (robustesse au schéma). Métropole = Lambert-93 (is_l93=True).
    gpkg = sqlite3.connect(path)
    gpkg.row_factory = sqlite3.Row
    try:
        cols = gpkg.execute(
            "SELECT table_name, column_name FROM gpkg_geometry_columns"
        ).fetchall()
        if not cols:
            raise PoiBdtopoImportError("aucune couche géométrique dans le GeoPackage")
        for c in cols:
            table, geom_col = c["table_name"], c["column_name"]
            for row in gpkg.execute(f'SELECT * FROM "{table}"'):
                d = dict(row)
                blob = d.pop(geom_col, None)
                if blob is None:
                    continue
                yield d, _decode_gpb(blob), True
    finally:
        gpkg.close()


def _iter_source(path: Path) -> Iterator[tuple[dict, BaseGeometry, bool]]:
    suffix = path.suffix.lower()
    if suffix in (".geojson", ".json"):
        return _read_geojson(path)
    if suffix in (".gpkg", ".sqlite"):
        return _read_geopackage(path)
    raise PoiBdtopoImportError(f"format non reconnu (attendu .geojson/.gpkg): {path}")


def _point_wgs84(geom: BaseGeometry, is_l93: bool) -> tuple[float, float]:
    """Point représentatif (lon, lat) WGS84 : centroïde, reprojeté si Lambert-93."""
    pt = geom.centroid
    if is_l93:
        pt = geo.to_wgs84_geom(pt)
    return pt.x, pt.y  # (lon, lat)


def import_poi_bdtopo(
    conn: sqlite3.Connection,
    source: str | Path,
    config: dict,
    *,
    imported_at: str | None = None,
) -> dict:
    """Importe/actualise les POI BD TOPO (idempotent par (`source`, `source_ref`)).

    Les enregistrements sans catégorie reconnue, sans `cleabs`, ou sans géométrie sont
    ignorés (comptés). Retourne un récap {upserted, skipped, by_category}.
    """
    rules = config.get("poi", {}).get("bdtopo_rules") or []
    if not rules:
        raise PoiBdtopoImportError("config [poi].bdtopo_rules absente ou vide")
    stamp = imported_at or _now_utc()

    path = Path(source)
    if not path.exists():
        raise PoiBdtopoImportError(f"source introuvable: {path}")

    upserted = 0
    skipped = 0
    by_category: dict[str, int] = {}
    for attrs, geom, is_l93 in _iter_source(path):
        category = _category(attrs, rules)
        if category is None:
            skipped += 1
            continue
        source_ref = _get(attrs, "cleabs", "id", "identifiant")
        if source_ref is None or geom.is_empty:
            skipped += 1
            continue
        lon, lat = _point_wgs84(geom, is_l93)
        conn.execute(
            "INSERT INTO poi (source, source_ref, category, nom, lat, lon, imported_at) "
            "VALUES ('bdtopo', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, source_ref) DO UPDATE SET "
            "category=excluded.category, nom=excluded.nom, lat=excluded.lat, "
            "lon=excluded.lon, imported_at=excluded.imported_at",
            (str(source_ref), category, _get(attrs, "toponyme", "nom", "name"),
             lat, lon, stamp),
        )
        upserted += 1
        by_category[category] = by_category.get(category, 0) + 1

    conn.commit()
    return {"upserted": upserted, "skipped": skipped, "by_category": by_category}
