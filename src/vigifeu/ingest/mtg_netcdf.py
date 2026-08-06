"""Parsing du netCDF MTG FCI L2 « Active Fire Monitoring » — groupe ListProduct (Spec 07 §2, étape 3).

Extrait la LISTE des pixels feu (position, heure, FRP, incertitude, confiance) et la filtre par bbox
France. La trame `QualityProduct` n'est pas traitée ici (v1). Seule fonction dépendante du format :
les noms de variables viennent de `[mtg.netcdf]` (listes de candidats — À CONFIRMER live, Spec 07 §12).

Entrée : des octets (téléchargés par `ingest/eumetsat.py`) ou un chemin de fichier. Sortie : une liste
de dicts `{lat, lon, acq_at, frp_mw, frp_uncertainty_mw, confidence}` prête pour l'ingestion (étape 4).
Aucun accès base ni réseau : fonction pure côté données, testable sur une fixture netCDF.
"""

from __future__ import annotations

from pathlib import Path

import netCDF4  # type: ignore[import-untyped]
import numpy as np


class MtgNetcdfError(Exception):
    pass


def _var(group, names: list[str]):
    """1re variable présente parmi les candidats (tolérant aux noms réels inconnus). None si aucune."""
    for name in names or []:
        if name in group.variables:
            return group.variables[name]
    return None


def _f(x) -> float | None:
    """Valeur flottante, ou None si masquée / NaN (donnée manquante du netCDF)."""
    if x is None or x is np.ma.masked:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def _s(x) -> str | None:
    """Confiance brute en texte (non normalisée, comme hotspot_raw.confidence). Entier propre si entier."""
    if x is None or x is np.ma.masked:
        return None
    if isinstance(x, (np.floating, float)) and float(x).is_integer():
        return str(int(x))
    if isinstance(x, (np.integer, int)):
        return str(int(x))
    return str(x).strip() or None


def _bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """« O,S,E,N » (format [mtg].bbox / FIRMS) → (ouest, sud, est, nord). None si absent/malformé."""
    if not bbox:
        return None
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise MtgNetcdfError(f"bbox malformée : {bbox!r}")
    w, s, e, n = (float(p) for p in parts)
    return w, s, e, n


def _acq_times(group, names: list[str], n: int, default_acq_at: str | None) -> list[str | None]:
    """Heures d'acquisition par pixel (ISO UTC). Décode un temps CF (`units`) via num2date ; à défaut
    de variable exploitable, retombe sur `default_acq_at` (l'heure du slot fournie par le listing)."""
    var = _var(group, names)
    if var is not None:
        try:
            vals = np.atleast_1d(var[:])
            units = getattr(var, "units", None)
            if units:
                cal = getattr(var, "calendar", "standard")
                dts = np.atleast_1d(netCDF4.num2date(vals, units, cal))
                iso = [d.strftime("%Y-%m-%dT%H:%M:%SZ") for d in dts]
            else:  # déjà des chaînes ISO ?
                iso = [str(v).strip() or None for v in vals]
            if len(iso) == n:
                return iso
            if len(iso) >= 1:  # temps unique (slot) → appliqué à tous les pixels
                return [iso[0]] * n
        except Exception:  # noqa: BLE001 — temps illisible : on retombe sur le default, jamais bloquant
            pass
    return [default_acq_at] * n


def parse_listproduct(
    source: bytes | bytearray | str | Path,
    config: dict,
    *,
    bbox: str | None = None,
    default_acq_at: str | None = None,
) -> list[dict]:
    """Pixels feu du netCDF, filtrés par `bbox`. `default_acq_at` = heure du slot (repli si pas de temps).

    `source` : octets (cas du fetcher) ou chemin. Lève `MtgNetcdfError` si lat/lon introuvables.
    """
    nc = config["mtg"]["netcdf"]
    if isinstance(source, (bytes, bytearray)):
        ds = netCDF4.Dataset("inmemory.nc", mode="r", memory=bytes(source))
    else:
        ds = netCDF4.Dataset(str(source), mode="r")
    try:
        group = ds.groups.get(nc.get("group")) or ds  # repli racine si le groupe n'existe pas
        lat_v, lon_v = _var(group, nc["lat"]), _var(group, nc["lon"])
        if lat_v is None or lon_v is None:
            raise MtgNetcdfError("variables lat/lon introuvables dans le netCDF (cf. [mtg.netcdf])")
        lat, lon = np.atleast_1d(lat_v[:]), np.atleast_1d(lon_v[:])
        n = len(lat)
        frp_v = _var(group, nc.get("frp", []))
        frpu_v = _var(group, nc.get("frp_uncertainty", []))
        conf_v = _var(group, nc.get("confidence", []))
        frp = np.atleast_1d(frp_v[:]) if frp_v is not None else None
        frpu = np.atleast_1d(frpu_v[:]) if frpu_v is not None else None
        conf = np.atleast_1d(conf_v[:]) if conf_v is not None else None
        acq = _acq_times(group, nc.get("time", []), n, default_acq_at)
        box = _bbox(bbox)

        out: list[dict] = []
        for i in range(n):
            la, lo = _f(lat[i]), _f(lon[i])
            if la is None or lo is None:
                continue
            if box and not (box[0] <= lo <= box[2] and box[1] <= la <= box[3]):
                continue
            out.append(
                {
                    "lat": la,
                    "lon": lo,
                    "acq_at": acq[i],
                    "frp_mw": _f(frp[i]) if frp is not None else None,
                    "frp_uncertainty_mw": _f(frpu[i]) if frpu is not None else None,
                    "confidence": _s(conf[i]) if conf is not None else None,
                }
            )
        return out
    finally:
        ds.close()
