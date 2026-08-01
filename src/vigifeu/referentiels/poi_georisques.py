"""Import Géorisques du référentiel POI — sites Seveso (Spec 06 §2.2/§8, étape 8).

**Périmètre v1 (décision 2026-08-01) : Seveso SEUL** (seuil haut/bas), pas les ICPE simples :
sous-ensemble à fort enjeu, bien géolocalisé, qui colle à la cible « exploitants de sites »
(Spec 05). Les ICPE simples ont une géoloc souvent grossière (§2.3) → reportées v1.1.

**Source réelle = API JSON Géorisques** (vérifiée live 2026-08-01), PAS un CSV comme le
supposait la 1ʳᵉ version (hypothèse de format corrigée sur la vraie donnée, comme le tag
`camp_site` d'OSM) : `https://www.georisques.gouv.fr/api/v1/installations_classees`, paginée,
retourne `{data: [ {record}, … ]}`. Champs en **camelCase** :

- `statutSeveso` : « Seveso seuil haut » / « Seveso seuil bas » / « Non Seveso » / null ;
- `codeAIOT`     : clé naturelle (source_ref) ;
- `raisonSociale`: nom de l'établissement ;
- `longitude` / `latitude` : WGS84 (présents la plupart du temps) ;
- `coordonneeXAIOT` / `coordonneeYAIOT` (+ `systemeCoordonneesAIOT` = "2154") : Lambert-93,
  utilisés en repli quand longitude/latitude manquent.

Le filtre serveur `statutSeveso` n'accepte pas la valeur affichée → on tire toute la base
et on filtre **côté client** sur le libellé (config `[poi].georisques_seveso_statuts`). Ops :
paginer l'API en un fichier `{data:[…]}` (ou concaténer les pages), puis `import_poi_georisques`.
Upsert idempotent par (`source='georisques'`, `source_ref`=codeAIOT). Catégorie `icpe_seveso`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Point

from vigifeu.engine import geo

CATEGORY = "icpe_seveso"


class PoiGeorisquesImportError(Exception):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(rec: dict, *keys: str):
    """Lecture tolérante (camelCase attendu, mais on reste souple sur les variantes)."""
    lowered = {k.lower(): v for k, v in rec.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def _to_float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


def _is_seveso(statut, accepted: list[str]) -> bool:
    """Vrai si le statut Seveso figure parmi les valeurs acceptées (config). Comparaison
    insensible à la casse, par sous-chaîne (« Seveso seuil haut (AS) » matcherait aussi)."""
    if not statut:
        return False
    s = str(statut).strip().lower()
    return any(tok in s for tok in accepted)


def _coords(rec: dict) -> tuple[float, float] | None:
    """(lat, lon) WGS84 : longitude/latitude directes, sinon coordonnee[XY]AIOT (Lambert-93)."""
    lat = _to_float(_get(rec, "latitude"))
    lon = _to_float(_get(rec, "longitude"))
    if lat is not None and lon is not None:
        return lat, lon
    x = _to_float(_get(rec, "coordonneeXAIOT", "coordonnee_x_aiot", "x_l93"))
    y = _to_float(_get(rec, "coordonneeYAIOT", "coordonnee_y_aiot", "y_l93"))
    srs = str(_get(rec, "systemeCoordonneesAIOT") or "2154")
    if x is not None and y is not None and srs == "2154":
        p = geo.to_wgs84_geom(Point(x, y))
        return p.y, p.x  # (lat, lon)
    return None


def _records(source: Path) -> list[dict]:
    """Récupère la liste des installations depuis un JSON Géorisques.

    Accepte la forme API `{data: [...]}` (une page ou plusieurs concaténées) ou une
    liste nue `[...]`. Pas d'appel réseau ici : l'assemblage des pages est un geste ops.
    """
    data = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        recs = data.get("data")
        if recs is None:
            raise PoiGeorisquesImportError("JSON Géorisques sans clé 'data'")
        return recs
    if isinstance(data, list):
        return data
    raise PoiGeorisquesImportError(f"format JSON inattendu: {type(data).__name__}")


def import_poi_georisques(
    conn: sqlite3.Connection,
    source: str | Path,
    config: dict,
    *,
    imported_at: str | None = None,
) -> dict:
    """Importe/actualise les sites Seveso Géorisques (idempotent par (`source`, `source_ref`)).

    Les installations non-Seveso, sans identifiant, ou sans coordonnées exploitables sont
    ignorées (comptées). Retourne {upserted, skipped}.
    """
    accepted = [
        s.strip().lower()
        for s in (config.get("poi", {}).get("georisques_seveso_statuts") or [])
    ]
    if not accepted:
        raise PoiGeorisquesImportError("config [poi].georisques_seveso_statuts absente ou vide")
    stamp = imported_at or _now_utc()

    path = Path(source)
    if not path.exists():
        raise PoiGeorisquesImportError(f"source introuvable: {path}")

    upserted = 0
    skipped = 0
    for rec in _records(path):
        if not _is_seveso(_get(rec, "statutSeveso", "statut_seveso", "seveso"), accepted):
            skipped += 1
            continue
        source_ref = _get(rec, "codeAIOT", "code_aiot", "identifiant", "id")
        coords = _coords(rec)
        if source_ref is None or coords is None:
            skipped += 1
            continue
        lat, lon = coords
        nom = _get(rec, "raisonSociale", "nom_ets", "nom_etablissement", "nom")
        conn.execute(
            "INSERT INTO poi (source, source_ref, category, nom, lat, lon, imported_at) "
            "VALUES ('georisques', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, source_ref) DO UPDATE SET "
            "category=excluded.category, nom=excluded.nom, lat=excluded.lat, "
            "lon=excluded.lon, imported_at=excluded.imported_at",
            (str(source_ref), CATEGORY, nom, lat, lon, stamp),
        )
        upserted += 1

    conn.commit()
    return {"upserted": upserted, "skipped": skipped}
