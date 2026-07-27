"""Déduplication inter-satellites par passage (Spec 02 §6).

Deux satellites (SNPP, NOAA-20/21) survolent la France à quelques minutes
d'écart et voient le **même point physique** : sans regroupement, ce point
compterait 2 à 4 fois. Tous les comptages de la qualification (§5) et les totaux
de FRP des versions (§6) portent sur les hotspots **dédupliqués** — sinon l'ajout
d'un satellite dégraderait mécaniquement l'interprétation.

Règle : deux hotspots de **satellites différents** dont les acquisitions sont à
moins de `dedup.window_min` et les pixels à moins de `dedup.radius_m` désignent le
même point → même `dedup_group`. La transitivité est gérée par union-find (un pixel
SNPP peut relier un NOAA-20 et un NOAA-21 vus chacun à < 20 min de lui).

Fonction **pure et recalculable** (P2) : ne touche pas la base, opère sur un nuage
de hotspots fourni. Appelée à l'identique par la qualification et par le versionnage.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Mapping, Sequence

from vigifeu.engine import geo

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO)


def dedup_groups(hotspots: Sequence[Mapping], config: dict) -> dict[int, str]:
    """{hotspot_id: dedup_group} pour un nuage de hotspots.

    Chaque groupe (`gNNN`, NNN = plus petit id membre) rassemble les pixels d'un
    même point physique vus par des satellites différents. Un hotspot sans jumeau
    inter-satellite forme un groupe singleton. `hotspots` : lignes portant
    `id, source_id, lat, lon, acq_at` (sqlite3.Row ou dict).
    """
    hs = list(hotspots)
    if not hs:
        return {}

    window_s = config["dedup"]["window_min"] * 60
    radius_m = config["dedup"]["radius_m"]

    parent = {h["id"]: h["id"] for h in hs}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # racine = plus petit id (clé stable)

    proj = geo.project_rows([(h["id"], h["lat"], h["lon"]) for h in hs])
    times = {h["id"]: _parse(h["acq_at"]) for h in hs}

    for i in range(len(hs)):
        a = hs[i]
        for j in range(i + 1, len(hs)):
            b = hs[j]
            if a["source_id"] == b["source_id"]:
                continue  # dédup strictement inter-satellites
            if abs((times[a["id"]] - times[b["id"]]).total_seconds()) >= window_s:
                continue
            (xa, ya), (xb, yb) = proj[a["id"]], proj[b["id"]]
            if math.hypot(xb - xa, yb - ya) >= radius_m:
                continue
            union(a["id"], b["id"])

    return {h["id"]: f"g{find(h['id'])}" for h in hs}


def count_dedup(groups: Mapping[int, str]) -> int:
    """Nombre de points physiques distincts (groupes distincts)."""
    return len(set(groups.values()))


def representative_ids(hotspots: Sequence[Mapping], groups: Mapping[int, str]) -> set[int]:
    """Un id par groupe : le hotspot de FRP le plus fort (signal le plus complet).

    Base des totaux de FRP et du FRP unitaire médian dédupliqués (§5, §6) : on
    somme/médiane sur ces représentants, pas sur tous les pixels.
    """
    best: dict[str, tuple[float, int]] = {}
    for h in hotspots:
        g = groups[h["id"]]
        frp = h["frp_mw"] if h["frp_mw"] is not None else 0.0
        cur = best.get(g)
        if cur is None or frp > cur[0]:
            best[g] = (frp, h["id"])
    return {hid for _, hid in best.values()}
