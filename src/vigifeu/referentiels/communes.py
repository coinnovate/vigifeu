"""Import du référentiel `commune` (Spec 01 §5.2).

Deux formats de source, une seule normalisation :

- **GeoPackage** (production : Admin Express COG CARTO, IGN) — un GeoPackage EST une
  base SQLite ; on le lit avec `sqlite3` stdlib et on décode les géométries en pur
  Python (en-tête GPB + WKB via `shapely.wkb`). Aucune dépendance native (pas de
  GDAL/geopandas) — cohérent avec le principe « minimum de pièces mobiles » (plan §1.1).
  Géométries en Lambert-93 (EPSG:2154), reprojetées en WGS84 pour le stockage.
- **GeoJSON** (fixture de test : extrait geo.api.gouv.fr) — déjà en WGS84.

Le stockage suit le module `geo` : géométrie en **WGS84 WKT** (cohérent avec
hotspot_raw.lat/lon), calculs métriques (aire, centroïde) en **Lambert-93**.

Décisions de cadrage Lot 3 : géométrie **généralisée**, une seule colonne ;
`surface_forestiere_ha`, `pprif`, `commune_succession` reportés (colonnes NULL).
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Iterator

from shapely import wkb as shapely_wkb
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from vigifeu.engine import geo


class CommuneImportError(Exception):
    pass


# --- normalisation des attributs (multi-schémas : geo.api.gouv.fr / Admin Express) ---

def _pick(raw: dict, *keys: str):
    for k in keys:
        v = raw.get(k)
        if v not in (None, ""):
            return v
    return None


def _dept_from_code(code: str) -> str:
    # Corse : 2A/2B (code_insee commence par 2A/2B). DROM : 971–976 (préfixe 3 car.).
    return code[:3] if code[:2] in ("97", "98") else code[:2]


def _normalize(raw: dict) -> dict:
    code = _pick(raw, "code", "INSEE_COM", "insee_com", "code_insee")
    if not code:
        raise CommuneImportError(f"enregistrement sans code INSEE: {raw!r}")
    dept = _pick(raw, "codeDepartement", "INSEE_DEP", "insee_dep") or _dept_from_code(code)
    pop = _pick(raw, "population", "POPULATION")
    return {
        "code_insee": code,
        "nom": _pick(raw, "nom", "NOM", "NOM_COM", "nom_com") or code,
        "dept": dept,
        "region": _pick(raw, "codeRegion", "INSEE_REG", "insee_reg"),
        "epci_code": _pick(raw, "codeEpci", "SIREN_EPCI", "siren_epci"),
        "population": int(pop) if pop not in (None, "") else None,
    }


def slugify(nom: str) -> str:
    """Slug d'URL pour une commune : minuscules, sans accents, tirets (Spec 01 §5.2).

    « Lège-Cap-Ferret » → « lege-cap-ferret » ; « Saint-Jean-d'Illac » → « saint-jean-d-illac ».
    """
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# --- lecture GeoJSON (fixture) ---

def _read_geojson(path: Path) -> Iterator[tuple[dict, BaseGeometry]]:
    fc = json.loads(path.read_text(encoding="utf-8"))
    for feat in fc.get("features", []):
        g = feat.get("geometry")
        if not g:
            continue
        yield _normalize(feat.get("properties", {})), shape(g)  # WGS84


# --- lecture GeoPackage (production, pur Python) ---

_GPB_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _decode_gpb(blob: bytes) -> BaseGeometry:
    """Décode une géométrie GeoPackage Binary (en-tête GPB + WKB standard).

    En-tête : 'GP' (2) + version (1) + flags (1) + srs_id (4) + enveloppe (variable).
    Les bits 1–3 des flags donnent la taille de l'enveloppe ; le WKB suit, avec son
    propre octet d'endianness (géré par shapely.wkb).
    """
    b = bytes(blob)
    if b[:2] != b"GP":
        return shapely_wkb.loads(b)  # déjà du WKB nu (tolérance)
    env_indicator = (b[3] >> 1) & 0x07
    header_len = 8 + _GPB_ENVELOPE_BYTES.get(env_indicator, 0)
    return shapely_wkb.loads(b[header_len:])


def _read_geopackage(path: Path, layer: str | None) -> Iterator[tuple[dict, BaseGeometry]]:
    gpkg = sqlite3.connect(path)
    gpkg.row_factory = sqlite3.Row
    try:
        cols = gpkg.execute(
            "SELECT table_name, column_name FROM gpkg_geometry_columns"
        ).fetchall()
        if not cols:
            raise CommuneImportError(f"aucune couche géométrique dans {path}")
        chosen = None
        for c in cols:
            if layer and c["table_name"].lower() == layer.lower():
                chosen = c
                break
            if layer is None and "commune" in c["table_name"].lower():
                chosen = c
                break
        if chosen is None:
            if layer is not None:
                raise CommuneImportError(f"couche '{layer}' absente de {path}")
            if len(cols) == 1:
                chosen = cols[0]
            else:
                noms = ", ".join(c["table_name"] for c in cols)
                raise CommuneImportError(
                    f"plusieurs couches ({noms}) ; préciser layer= pour {path}"
                )
        table, geom_col = chosen["table_name"], chosen["column_name"]
        for row in gpkg.execute(f'SELECT * FROM "{table}"'):
            d = dict(row)
            blob = d.pop(geom_col, None)
            if blob is None:
                continue
            g_l93 = _decode_gpb(blob)
            yield _normalize(d), geo.to_wgs84_geom(g_l93)
    finally:
        gpkg.close()


# --- import ---

def _iter_source(path: Path, layer: str | None) -> Iterator[tuple[dict, BaseGeometry]]:
    suffix = path.suffix.lower()
    if suffix in (".geojson", ".json"):
        return _read_geojson(path)
    if suffix in (".gpkg", ".sqlite"):
        return _read_geopackage(path, layer)
    raise CommuneImportError(f"format non reconnu (attendu .geojson/.gpkg): {path}")


def import_communes(
    conn: sqlite3.Connection,
    source: str | Path,
    *,
    millesime: str,
    layer: str | None = None,
) -> dict:
    """Importe/actualise le référentiel commune depuis un fichier (idempotent).

    Géométrie stockée en WGS84 WKT ; `surface_ha` et le centroïde calculés en
    Lambert-93 (aire juste, centroïde reprojeté). Upsert par code_insee : rejouer
    un millésime met à jour sans dupliquer.
    """
    path = Path(source)
    if not path.exists():
        raise CommuneImportError(f"source introuvable: {path}")

    n = 0
    for attrs, geom_wgs84 in _iter_source(path, layer):
        geom_l93 = geo.to_l93_geom(geom_wgs84)
        c_l93 = geom_l93.centroid
        c_lon, c_lat = geo.to_wgs84_geom(c_l93).coords[0]
        conn.execute(
            """INSERT INTO commune
                 (code_insee, slug, nom, dept, region, epci_code, population,
                  geometry_wkt, centroid_lat, centroid_lon, surface_ha, referentiel_millesime)
               VALUES (:code_insee, :slug, :nom, :dept, :region, :epci_code, :population,
                       :geometry_wkt, :centroid_lat, :centroid_lon, :surface_ha, :millesime)
               ON CONFLICT(code_insee) DO UPDATE SET
                  slug=excluded.slug, nom=excluded.nom, dept=excluded.dept,
                  region=excluded.region, epci_code=excluded.epci_code,
                  population=excluded.population, geometry_wkt=excluded.geometry_wkt,
                  centroid_lat=excluded.centroid_lat, centroid_lon=excluded.centroid_lon,
                  surface_ha=excluded.surface_ha,
                  referentiel_millesime=excluded.referentiel_millesime""",
            {
                **attrs,
                "slug": slugify(attrs["nom"]),
                "geometry_wkt": geom_wgs84.wkt,
                "centroid_lat": c_lat,
                "centroid_lon": c_lon,
                "surface_ha": geom_l93.area / 10_000.0,
                "millesime": millesime,
            },
        )
        n += 1
    conn.commit()
    return {"imported": n, "millesime": millesime}
