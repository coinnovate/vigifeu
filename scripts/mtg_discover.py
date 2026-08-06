"""Découverte du produit MTG FCI L2 « Active Fire Monitoring » (EO:EUM:DAT:0682).

Étape 10 de la Spec 07 (calage prod, hors-code) : confirmer contre l'API RÉELLE les deux derniers
inconnus laissés en hypothèses (§12) —
  1. l'URL exacte de recherche/téléchargement du Data Store  → `[mtg].data_url`
  2. les noms de variables du groupe netCDF `ListProduct`      → `[mtg.netcdf]`

Ne modifie RIEN : lit les identifiants dans l'environnement, tire un granule récent, imprime la
structure de la réponse et du netCDF, et suggère les valeurs de config à poser. Rejouable sur le VPS.

Usage (identifiants en environnement, jamais en dur) :
    export EUMETSAT_CONSUMER_KEY=...     # (Windows : set EUMETSAT_CONSUMER_KEY=...)
    export EUMETSAT_CONSUMER_SECRET=...
    python scripts/mtg_discover.py [heures_en_arriere]     # défaut : 24 h

Dépendances déjà présentes : httpx, netCDF4 (pyproject).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta

import httpx

TOKEN_URL = "https://api.eumetsat.int/token"
BASE = "https://api.eumetsat.int"
COLLECTION = "EO:EUM:DAT:0682"
BBOX = "-5.5,41.0,10.0,51.5"          # France métro + marge (= [general].firms_bbox)

# Endpoints de recherche CANDIDATS (on prend le premier qui répond 200) — c'est justement ce
# qu'on cherche à confirmer. La forme OpenSearch du Data Store a varié selon les versions.
SEARCH_CANDIDATS = [
    f"{BASE}/data/search-products/os",
    f"{BASE}/data/search-products/1.0.0/os",
]


def _creds() -> tuple[str, str]:
    key = os.environ.get("EUMETSAT_CONSUMER_KEY")
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        sys.exit("ERREUR : EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET absents de l'environnement.")
    return key, secret


def _token(key: str, secret: str) -> str:
    r = httpx.post(TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(key, secret), timeout=60)
    r.raise_for_status()
    tok = r.json().get("access_token")
    if not tok:
        sys.exit(f"ERREUR : pas d'access_token dans la réponse : {r.text[:300]}")
    print(f"[1/4] Token OAuth2 obtenu (expire dans {r.json().get('expires_in')} s).")
    return tok


def _search(token: str, since_h: int) -> tuple[str, dict]:
    now = datetime.now(UTC)
    params = {
        "format": "json",
        "pi": COLLECTION,
        "dtstart": (now - timedelta(hours=since_h)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dtend": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bbox": BBOX,
    }
    headers = {"Authorization": f"Bearer {token}"}
    for url in SEARCH_CANDIDATS:
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=120, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"      {url} → {type(exc).__name__}")
            continue
        print(f"      {url} → HTTP {r.status_code}")
        if r.status_code == 200:
            payload = r.json()
            n = len(payload.get("features") or [])
            print(f"[2/4] Endpoint de recherche VALIDE : {url}  ({n} produit(s) sur {since_h} h)")
            return url, payload
    sys.exit("ERREUR : aucun endpoint de recherche candidat n'a répondu 200. Élargir la fenêtre "
             "(argument heures) ou vérifier le lien de migration d'auth.")


def _inspecter_produit(payload: dict) -> str | None:
    feats = payload.get("features") or []
    if not feats:
        print("      Aucun produit sur la fenêtre — relance avec plus d'heures, ex. : "
              "python scripts/mtg_discover.py 72")
        return None
    f0 = feats[0]
    print("\n--- Structure du 1er produit (à confronter à parse_product_list) ---")
    print("id        :", f0.get("id"))
    props = f0.get("properties") or {}
    print("properties keys :", list(props.keys()))
    print("date      :", props.get("date"))
    liens = (props.get("links") or {})
    print("links keys:", list(liens.keys()))
    data_links = liens.get("data") or []
    href = data_links[0].get("href") if data_links and isinstance(data_links[0], dict) else None
    print("download  :", href)
    return href


def _telecharger(token: str, href: str) -> str:
    print("\n[3/4] Téléchargement du granule…")
    r = httpx.get(href, headers={"Authorization": f"Bearer {token}"}, timeout=300, follow_redirects=True)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".nc", prefix="mtg0682_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(r.content)
    print(f"      {len(r.content)} octets → {path}")
    return path


def _extract_nc(path: str) -> str:
    """Le Data Store livre un ZIP (SIP) contenant le netCDF + métadonnées. On en extrait le .nc."""
    import zipfile

    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic[:4] == b"\x89HDF" or magic[:3] == b"CDF":
        return path  # déjà un netCDF brut
    if magic[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            ncs = [n for n in z.namelist() if n.lower().endswith(".nc")]
            print("      Archive ZIP (SIP) — entrées :", z.namelist())
            print("      → fichier(s) .nc :", ncs)
            if not ncs:
                sys.exit("ERREUR : aucun .nc dans l'archive SIP.")
            out = os.path.join(tempfile.gettempdir(), os.path.basename(ncs[0]))
            with z.open(ncs[0]) as src, open(out, "wb") as dst:
                dst.write(src.read())
            return out
    sys.exit(f"ERREUR : format inconnu (magic={magic!r}).")


def _dump_netcdf(path: str) -> None:
    import netCDF4  # import tardif : n'échoue que si on va jusqu'au dump

    path = _extract_nc(path)
    print("\n[4/4] Structure netCDF (groupes + variables) :")
    ds = netCDF4.Dataset(path, "r")
    try:
        print("GROUPES :", list(ds.groups))
        for gname in (list(ds.groups) or [None]):
            g = ds.groups[gname] if gname else ds
            libelle = gname or "(racine)"
            print(f"\n  Groupe « {libelle} » — variables :")
            for vn, v in g.variables.items():
                units = getattr(v, "units", "")
                dims = ",".join(v.dimensions)
                print(f"    - {vn:32s} [{dims}] {('units=' + units) if units else ''}")
    finally:
        ds.close()

    print("\n=== À reporter dans config/params.toml ===")
    print("[mtg]        data_url  = base de l'endpoint qui a marché ci-dessus (sans /search-products/...)")
    print("[mtg.netcdf] group / lat / lon / time / frp / frp_uncertainty / confidence :")
    print("             mets les VRAIS noms de variables du groupe ListProduct listés au [4/4].")
    print("Puis : figer une fixture tests/fixtures/mtg/, valider sur Saumos, activated=true.")


def main() -> None:
    since_h = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    key, secret = _creds()
    token = _token(key, secret)
    _url, payload = _search(token, since_h)
    href = _inspecter_produit(payload)
    if not href:
        return
    path = _telecharger(token, href)
    _dump_netcdf(path)


if __name__ == "__main__":
    main()
