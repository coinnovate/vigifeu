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
    # Multi-schémas : geo.api.gouv.fr (code, nom, codeDepartement…), Admin Express
    # ≤3-x (INSEE_COM, NOM, INSEE_DEP…) et Admin Express 4-0 / thème administratif
    # (code_insee, nom_officiel, code_insee_du_departement…).
    code = _pick(raw, "code", "code_insee", "INSEE_COM", "insee_com",
                 "code_insee_de_la_commune")
    if not code:
        raise CommuneImportError(f"enregistrement sans code INSEE: {raw!r}")
    dept = _pick(raw, "codeDepartement", "code_insee_du_departement",
                 "INSEE_DEP", "insee_dep") or _dept_from_code(code)
    pop = _pick(raw, "population", "POPULATION")
    return {
        "code_insee": code,
        "nom": _pick(raw, "nom", "nom_officiel", "NOM", "NOM_COM", "nom_com") or code,
        "dept": dept,
        "region": _pick(raw, "codeRegion", "code_insee_de_la_region", "INSEE_REG", "insee_reg"),
        "epci_code": _pick(raw, "codeEpci", "codes_siren_des_epci", "code_siren_de_l_epci",
                           "SIREN_EPCI", "siren_epci"),
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

def _read_geojson(path: Path) -> Iterator[tuple[dict, BaseGeometry, bool]]:
    # Triplet (attrs, géométrie, is_l93) : GeoJSON = WGS84 (is_l93=False).
    fc = json.loads(path.read_text(encoding="utf-8"))
    for feat in fc.get("features", []):
        g = feat.get("geometry")
        if not g:
            continue
        yield _normalize(feat.get("properties", {})), shape(g), False


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


_LAYER_EXCLURE = ("chef_lieu", "associee", "deleguee", "associee_ou_deleguee")


def _choose_layer(cols: list, layer: str | None) -> tuple[str, str]:
    """Sélectionne (table, colonne géométrie) de la couche communale.

    Admin Express contient une quinzaine de couches (commune, chef_lieu_de_commune,
    commune_associee_ou_deleguee, departement…). En auto, on prend la couche
    **`commune` exacte** (le polygone), jamais les chef-lieux (points) ni les communes
    associées/déléguées — sinon un `LIKE '%commune%'` naïf attrape chef_lieu_de_commune.
    """
    if not cols:
        raise CommuneImportError("aucune couche géométrique dans le GeoPackage")
    by_name = {c["table_name"].lower(): c for c in cols}
    if layer:
        c = by_name.get(layer.lower())
        if c is None:
            raise CommuneImportError(
                f"couche '{layer}' absente (couches: {', '.join(by_name)})"
            )
        return c["table_name"], c["column_name"]
    if "commune" in by_name:
        c = by_name["commune"]
        return c["table_name"], c["column_name"]
    for name, c in by_name.items():
        if "commune" in name and not any(k in name for k in _LAYER_EXCLURE):
            return c["table_name"], c["column_name"]
    if len(cols) == 1:
        return cols[0]["table_name"], cols[0]["column_name"]
    raise CommuneImportError(
        f"plusieurs couches, préciser layer= (couches: {', '.join(by_name)})"
    )


def _read_geopackage(path: Path, layer: str | None) -> Iterator[tuple[dict, BaseGeometry, bool]]:
    # Admin Express métropole = Lambert-93 (is_l93=True) : on livre la géométrie native,
    # sans reprojection dans le lecteur — import_communes reprojette une seule fois.
    gpkg = sqlite3.connect(path)
    gpkg.row_factory = sqlite3.Row
    try:
        cols = gpkg.execute(
            "SELECT table_name, column_name FROM gpkg_geometry_columns"
        ).fetchall()
        table, geom_col = _choose_layer(cols, layer)
        for row in gpkg.execute(f'SELECT * FROM "{table}"'):
            d = dict(row)
            blob = d.pop(geom_col, None)
            if blob is None:
                continue
            yield _normalize(d), _decode_gpb(blob), True
    finally:
        gpkg.close()


# --- import ---

def _iter_source(path: Path, layer: str | None) -> Iterator[tuple[dict, BaseGeometry, bool]]:
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
    progress_every: int = 5000,
) -> dict:
    """Importe/actualise le référentiel commune depuis un fichier (idempotent).

    Géométrie stockée en WGS84 WKT ; `surface_ha` et le centroïde calculés en
    Lambert-93 (aire juste, centroïde reprojeté). Upsert par code_insee : rejouer
    un millésime met à jour sans dupliquer. Une seule reprojection par commune ;
    progression affichée tous les `progress_every` (0 = silencieux). Un COMMIT
    périodique borne la transaction et rend un import interrompu partiellement utile.
    """
    path = Path(source)
    if not path.exists():
        raise CommuneImportError(f"source introuvable: {path}")

    n = 0
    for attrs, geom, is_l93 in _iter_source(path, layer):
        if is_l93:
            geom_l93 = geom
            geom_wgs84 = geo.to_wgs84_geom(geom_l93)
        else:
            geom_wgs84 = geom
            geom_l93 = geo.to_l93_geom(geom_wgs84)
        c_lon, c_lat = geo.to_wgs84_geom(geom_l93.centroid).coords[0]
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
        if progress_every and n % progress_every == 0:
            conn.commit()  # borne la transaction + point de reprise (upsert idempotent)
            print(f"  … {n} communes importées", flush=True)
    conn.commit()
    return {"imported": n, "millesime": millesime}
