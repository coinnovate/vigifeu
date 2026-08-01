"""Import Géorisques du référentiel POI — sites Seveso (Spec 06 §2.2/§8, étape 8).

Troisième source du référentiel POI. **Périmètre v1 (décision 2026-08-01) : Seveso SEUL**
(seuil haut/bas), pas les ICPE simples : ~1 300 sites bien géolocalisés à fort enjeu, qui
collent à la cible « exploitants de sites » (Spec 05). Les ICPE simples ont une géoloc
souvent grossière (centroïde commune, §2.3) → faux « dans la zone détectée » → reportées v1.1.

Source : `georisques.gouv.fr` / data.gouv.fr « Base des installations classées (ICPE) »,
export **CSV**, Licence ouverte, mise à jour quotidienne. Même mécanique de lecture tolérante
que `bdiff.py` (encodage cp1252/utf-8, séparateur ; ou ,). Catégorie unique `icpe_seveso`
(celle du lexique). Upsert idempotent par (`source='georisques'`, `source_ref`=code AIOT).

⚠️ HYPOTHÈSE DE FORMAT (comme bdiff/drought) : libellés de colonnes et de statut Seveso à
confirmer contre l'export réel. Toute la logique de mapping est isolée dans `_normalize_row`
et la config `[poi].georisques_seveso_statuts` — seuls points à ajuster.

Coordonnées : `longitude`/`latitude` (WGS84) si présentes, sinon `x`/`y` en Lambert-93
reprojetés — Géorisques livre les deux selon l'export.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from shapely.geometry import Point

from vigifeu.engine import geo

CATEGORY = "icpe_seveso"


class PoiGeorisquesImportError(Exception):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(raw: dict, *keys: str):
    for k in keys:
        for rk, v in raw.items():
            if rk and rk.strip().lower() == k.lower() and v not in (None, ""):
                return v
    return None


def _to_float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


def _is_seveso(statut: str | None, accepted: list[str]) -> bool:
    """Vrai si le statut Seveso figure parmi les valeurs acceptées (config). Comparaison
    insensible à la casse/accents, par sous-chaîne (« Seveso seuil haut (AS) » matche)."""
    if not statut:
        return False
    s = statut.strip().lower()
    return any(tok in s for tok in accepted)


def _coords(raw: dict) -> tuple[float, float] | None:
    """(lat, lon) WGS84 : longitude/latitude directes, sinon x/y Lambert-93 reprojetés."""
    lat = _to_float(_pick(raw, "latitude", "lat", "y_wgs84"))
    lon = _to_float(_pick(raw, "longitude", "lon", "lng", "x_wgs84"))
    if lat is not None and lon is not None:
        return lat, lon
    x = _to_float(_pick(raw, "x_l93", "x", "coordxlambert93", "x_lambert93"))
    y = _to_float(_pick(raw, "y_l93", "y", "coordylambert93", "y_lambert93"))
    if x is not None and y is not None:
        p = geo.to_wgs84_geom(Point(x, y))
        return p.y, p.x  # (lat, lon)
    return None


def _read_csv(path: Path) -> Iterator[dict]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PoiGeorisquesImportError(f"encodage illisible: {path}")
    lines = text.splitlines()
    sample = "\n".join(lines[:3])
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    yield from csv.DictReader(lines, delimiter=delim)


def import_poi_georisques(
    conn: sqlite3.Connection,
    source: str | Path,
    config: dict,
    *,
    imported_at: str | None = None,
) -> dict:
    """Importe/actualise les sites Seveso Géorisques (idempotent par (`source`, `source_ref`)).

    Les lignes non-Seveso, sans identifiant, ou sans coordonnées exploitables sont ignorées
    (comptées). Retourne {upserted, skipped}.
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
    for raw in _read_csv(path):
        statut = _pick(raw, "statut_seveso", "seveso", "regime_seveso", "statutSeveso")
        if not _is_seveso(statut, accepted):
            skipped += 1
            continue
        source_ref = _pick(raw, "code_aiot", "codeAIOT", "identifiant", "id", "code")
        coords = _coords(raw)
        if source_ref is None or coords is None:
            skipped += 1
            continue
        lat, lon = coords
        nom = _pick(raw, "nom_ets", "nom_etablissement", "raison_sociale", "nom", "toponyme")
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
