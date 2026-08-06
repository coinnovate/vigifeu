"""Accès au EUMETSAT Data Store — OAuth2 + listing/téléchargement (Spec 07 §2, étape 2).

Voie d'accès « style maison » (décision D3) : `httpx` + `tenacity` comme `ingest/firms.py`,
token OAuth2 fait main, PAS la lib `eumdac`. Ce module ne connaît QUE l'accès (authentifier,
lister, télécharger) ; le parsing du netCDF (étape 3) et l'orchestration/ingestion (étape 4)
vivent ailleurs.

Découpage (comme les autres fetchers) : le format de l'API n'est touché QUE par les fonctions
PURES `build_search_params` et `parse_product_list` — testables hors réseau. Le réseau (token,
GET) est isolé dans trois primitives bas niveau (`_token_request`, `_get_json`, `_get_bytes`),
monkeypatchables en test (même seam que `drought._fetch_json`).

Identifiants OAuth2 en ENVIRONNEMENT (`EUMETSAT_CONSUMER_KEY` / `EUMETSAT_CONSUMER_SECRET`),
jamais dans le dépôt (même discipline que `FIRMS_MAP_KEY`). Sans identifiants → EumetsatError.

⚠️ Endpoints/format à confirmer LIVE contre l'API réelle (Spec 07 §12) : l'URL de recherche et
la forme du JSON (`parse_product_list`) sont le point de vérification — d'où leur isolation.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_HTTP_REESSAYABLE = (429, 500, 502, 503, 504)
_TOKEN_MARGE_S = 60.0  # on rafraîchit le token 60 s avant son expiration réelle


class EumetsatError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Identifiants (environnement, jamais le dépôt)                                #
# --------------------------------------------------------------------------- #

def credentials() -> tuple[str, str]:
    """(consumer_key, consumer_secret) depuis l'environnement. Lève si absents (dégradé propre)."""
    key = os.environ.get("EUMETSAT_CONSUMER_KEY")
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        raise EumetsatError(
            "EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET absents de l'environnement"
        )
    return key, secret


# --------------------------------------------------------------------------- #
# Fonctions PURES (format de l'API) — testables sans réseau                    #
# --------------------------------------------------------------------------- #

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_search_params(config: dict, since: datetime, until: datetime) -> dict:
    """Paramètres de la requête de recherche des produits 0682 sur [since, until].

    Format OpenSearch du Data Store (à confirmer live) : collection (`pi`), fenêtre temporelle
    (`dtstart`/`dtend`) et emprise (`bbox` = O,S,E,N, réutilise `[mtg].bbox` au format FIRMS).
    """
    m = config["mtg"]
    return {
        "format": "json",
        "pi": m["collection_id"],
        "dtstart": _iso(since),
        "dtend": _iso(until),
        "bbox": m["bbox"],
    }


def parse_product_list(payload: dict) -> list[dict]:
    """Normalise la réponse de recherche en `[{product_id, sensing_at, download_url}]`, trié
    chronologiquement (ordre naturel d'ingestion).

    Forme attendue (OpenSearch/GeoJSON du Data Store, à confirmer live) : `features[]` portant
    `id`, `properties.date` (intervalle « début/fin » du slot) et `properties.links.data[].href`
    (lien de téléchargement). Une entrée sans lien exploitable est ignorée (tolérant).
    SEULE (avec build_search_params) fonction dépendante du format → point de vérification live.
    """
    produits: list[dict] = []
    for feat in payload.get("features") or []:
        props = feat.get("properties") or {}
        # `date` = "2026-08-06T12:40:00Z/2026-08-06T12:50:00Z" → on garde le début (le slot).
        date = props.get("date")
        sensing_at = date.split("/", 1)[0] if isinstance(date, str) and date else None
        liens = ((props.get("links") or {}).get("data")) or []
        href = None
        for lien in liens:
            if isinstance(lien, dict) and lien.get("href"):
                href = lien["href"]
                break
        if not href or not sensing_at:
            continue
        produits.append(
            {"product_id": feat.get("id"), "sensing_at": sensing_at, "download_url": href}
        )
    produits.sort(key=lambda p: p["sensing_at"])
    return produits


# --------------------------------------------------------------------------- #
# Réseau bas niveau — isolé, retryé, monkeypatchable en test                   #
# --------------------------------------------------------------------------- #

def _retrying(config: dict):
    """Décorateur tenacity commun (backoff borné sur réseau + 429/5xx), paramétré par `[mtg]`."""
    m = config["mtg"]
    return retry(
        stop=stop_after_attempt(m["max_retries"]),
        wait=wait_exponential(min=m["retry_wait_min_s"], max=m["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, EumetsatError)),
        reraise=True,
    )


def _token_request(config: dict, key: str, secret: str) -> dict:
    """POST OAuth2 `client_credentials` (Basic auth). Retourne le JSON {access_token, expires_in}."""
    m = config["mtg"]

    @_retrying(config)
    def _do() -> dict:
        resp = httpx.post(
            m["token_url"],
            data={"grant_type": "client_credentials"},
            auth=(key, secret),
            timeout=m["timeout_s"],
        )
        if resp.status_code in _HTTP_REESSAYABLE:
            raise EumetsatError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code != 200:
            raise EumetsatError(f"token HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    return _do()


def _get_json(config: dict, url: str, token: str, params: dict | None = None) -> dict:
    """GET authentifié renvoyant du JSON (recherche de produits)."""
    m = config["mtg"]

    @_retrying(config)
    def _do() -> dict:
        resp = httpx.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=m["timeout_s"],
            follow_redirects=True,
        )
        if resp.status_code in _HTTP_REESSAYABLE:
            raise EumetsatError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code != 200:
            raise EumetsatError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    return _do()


def _get_bytes(config: dict, url: str, token: str) -> bytes:
    """GET authentifié renvoyant des octets (téléchargement du netCDF)."""
    m = config["mtg"]

    @_retrying(config)
    def _do() -> bytes:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=m["timeout_s"],
            follow_redirects=True,
        )
        if resp.status_code in _HTTP_REESSAYABLE:
            raise EumetsatError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code != 200:
            raise EumetsatError(f"téléchargement HTTP {resp.status_code}")
        return resp.content

    return _do()


# --------------------------------------------------------------------------- #
# Client — token en cache + listing + téléchargement                          #
# --------------------------------------------------------------------------- #

class EumetsatClient:
    """Accès Data Store : gère le token OAuth2 (cache + rafraîchissement), liste et télécharge.

    Le token est mis en cache jusqu'à `expires_in - marge` ; les appels réseau passent par les
    primitives module (`_token_request`/`_get_json`/`_get_bytes`), remplaçables en test.
    """

    def __init__(self, config: dict):
        self._config = config
        self._token: tuple[str, float] | None = None  # (access_token, expire_at monotonic)

    def token(self) -> str:
        now = time.monotonic()
        if self._token and self._token[1] > now:
            return self._token[0]
        key, secret = credentials()
        data = _token_request(self._config, key, secret)
        access = data.get("access_token")
        if not access:
            raise EumetsatError("réponse de token sans access_token")
        ttl = float(data.get("expires_in", 3600))
        self._token = (access, now + max(0.0, ttl - _TOKEN_MARGE_S))
        return access

    def list_products(self, since: datetime, until: datetime) -> list[dict]:
        """Produits 0682 sur [since, until], triés chronologiquement (cf. parse_product_list)."""
        m = self._config["mtg"]
        url = f"{m['data_url']}/search-products/os"  # ⚠️ à confirmer live (Spec 07 §12)
        params = build_search_params(self._config, since, until)
        payload = _get_json(self._config, url, self.token(), params)
        return parse_product_list(payload)

    def download(self, download_url: str) -> bytes:
        """Télécharge le netCDF d'un produit (octets bruts, parsés à l'étape 3)."""
        return _get_bytes(self._config, download_url, self.token())
