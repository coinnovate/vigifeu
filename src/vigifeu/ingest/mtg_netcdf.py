"""Parsing du netCDF MTG FCI L2 « Active Fire Monitoring » — FIR (Spec 07 §2, étape 3, révisé prod).

Le vrai produit 0682 (confirmé sur un granule réel, 2026-08-06) n'est PAS une liste de pixels
(`ListProduct`), mais une **grille pleine-disque** en projection **géostationnaire à 0°** :
- `fire_result` [rows,cols] (int8) : classe de détection par pixel (0 pas de feu ; 1/2/3 feu par
  confiance croissante ; 4 hors-disque/non traité) — les classes « feu » sont configurables ;
- `fire_probability` [rows,cols] : probabilité 0-1 ;
- `x`, `y` : angles de balayage (radians) ; `mtg_geos_projection` : paramètres de projection ;
- il n'y a **PAS de FRP** (le FRP relève du produit LSA SAF démo/interne — Spec 07 §3/§6).

On extrait les pixels dont `fire_result` ∈ classes-feu, on les **déprojette** (x,y géostationnaires →
lon/lat WGS84 via pyproj), on filtre par bbox France, et on retourne `{lat, lon, acq_at, frp_mw(None),
confidence(=classe), probability}`. Le produit est livré en **ZIP (SIP)** : on en extrait le `.nc`.
Aucun accès base ni réseau : fonction pure côté données, testable sur une fixture netCDF.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import netCDF4  # type: ignore[import-untyped]
import numpy as np
from pyproj import CRS, Transformer


class MtgNetcdfError(Exception):
    pass


def _nc_bytes(data: bytes) -> bytes:
    """Retourne les octets du netCDF : extrait le `.nc` si `data` est une archive SIP (ZIP)."""
    if data[:2] == b"PK":  # signature ZIP → SIP EUMETSAT
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if name.lower().endswith(".nc"):
                    return z.read(name)
        raise MtgNetcdfError("archive SIP sans fichier .nc")
    return data  # déjà un netCDF brut (\x89HDF… ou CDF…)


def _f(x) -> float | None:
    if x is None or x is np.ma.masked:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def _bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise MtgNetcdfError(f"bbox malformée : {bbox!r}")
    w, s, e, n = (float(p) for p in parts)
    return w, s, e, n


def _acq_from_global(ds, default_acq_at: str | None, attr: str) -> str | None:
    """Heure du slot depuis un attribut global `YYYYMMDDHHMMSS` (ex. time_coverage_start) → ISO UTC.
    Repli sur `default_acq_at` (heure du produit fournie par le listing)."""
    raw = getattr(ds, attr, None)
    if isinstance(raw, str) and len(raw) == 14 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T{raw[8:10]}:{raw[10:12]}:{raw[12:14]}Z"
    return default_acq_at


def _geos_transformer(proj_var) -> Transformer:
    """Transformer géostationnaire (grille MTG) → WGS84, depuis les attributs CF de la projection."""
    crs = CRS.from_proj4(
        f"+proj=geos +h={float(proj_var.perspective_point_height)} "
        f"+lon_0={float(proj_var.longitude_of_projection_origin)} "
        f"+a={float(proj_var.semi_major_axis)} +b={float(proj_var.semi_minor_axis)} "
        f"+sweep={getattr(proj_var, 'sweep_angle_axis', 'y')} +units=m +no_defs +type=crs"
    )
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def parse_fir(
    source: bytes | bytearray | str | Path,
    config: dict,
    *,
    bbox: str | None = None,
    default_acq_at: str | None = None,
) -> list[dict]:
    """Pixels feu du netCDF FIR (grille géostationnaire), déprojetés et filtrés par `bbox`.

    `source` : octets (ZIP SIP ou netCDF, cas du fetcher) ou chemin. `default_acq_at` = heure du slot
    (repli). Lève `MtgNetcdfError` si les variables attendues sont absentes.
    """
    nc = config["mtg"]["netcdf"]
    if isinstance(source, (bytes, bytearray)):
        ds = netCDF4.Dataset("inmemory.nc", mode="r", memory=_nc_bytes(bytes(source)))
    else:
        ds = netCDF4.Dataset(str(source), mode="r")
    try:
        for key in (nc["proj"], nc["x"], nc["y"], nc["fire_result"]):
            if key not in ds.variables:
                raise MtgNetcdfError(f"variable '{key}' absente du netCDF (cf. [mtg.netcdf])")
        # netCDF4 applique automatiquement scale_factor/add_offset → x,y déjà en radians.
        x = np.asarray(ds.variables[nc["x"]][:], dtype="float64")
        y = np.asarray(ds.variables[nc["y"]][:], dtype="float64")
        fire = np.asarray(ds.variables[nc["fire_result"]][:])
        prob_v = ds.variables.get(nc.get("probability"))
        prob = np.ma.asarray(prob_v[:]) if prob_v is not None else None
        acq = _acq_from_global(ds, default_acq_at, nc.get("time_attr", "time_coverage_start"))

        rows, cols = np.where(np.isin(fire, list(nc["fire_classes"])))
        if len(rows) == 0:
            return []
        h = float(ds.variables[nc["proj"]].perspective_point_height)
        transformer = _geos_transformer(ds.variables[nc["proj"]])
        lons, lats = transformer.transform(x[cols] * h, y[rows] * h)

        box = _bbox(bbox)
        out: list[dict] = []
        for k in range(len(rows)):
            la, lo = float(lats[k]), float(lons[k])
            if not (np.isfinite(la) and np.isfinite(lo)):
                continue  # pixel hors-disque (déprojection infinie)
            if box and not (box[0] <= lo <= box[2] and box[1] <= la <= box[3]):
                continue
            p = None
            if prob is not None:
                pv = prob[rows[k], cols[k]]
                p = None if pv is np.ma.masked else _f(pv)
            out.append({
                "lat": la,
                "lon": lo,
                "acq_at": acq,
                "frp_mw": None,                 # le 0682 ne porte PAS de FRP (§6)
                "frp_uncertainty_mw": None,
                "confidence": str(int(fire[rows[k], cols[k]])),   # classe de détection (1/2/3)
                "probability": p,               # 0-1, conservée dans raw_payload à l'ingestion
            })
        return out
    finally:
        ds.close()
