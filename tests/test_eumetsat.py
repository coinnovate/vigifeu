"""Tests de l'accès EUMETSAT Data Store (Spec 07 §2, étape 2).

Réseau simulé : on monkeypatch les trois primitives bas niveau (`_token_request`, `_get_json`,
`_get_bytes`) — aucun appel réel. On couvre : identifiants manquants (dégradé), cache + rafraîchis-
sement du token, parsing pur de la liste de produits, construction des paramètres de recherche,
et le bout-en-bout listing/téléchargement.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.ingest import eumetsat
from vigifeu.model.db import load_config

SINCE = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
UNTIL = datetime(2026, 8, 6, 13, 0, 0, tzinfo=UTC)

# Réponse de recherche factice (forme OpenSearch/GeoJSON du Data Store).
SEARCH_PAYLOAD = {
    "features": [
        {
            "id": "MTGFCI-0682-B-slot2",
            "properties": {
                "date": "2026-08-06T12:50:00Z/2026-08-06T13:00:00Z",
                "links": {"data": [{"href": "https://api.eumetsat.int/data/download/slot2.nc"}]},
            },
        },
        {
            "id": "MTGFCI-0682-A-slot1",
            "properties": {
                "date": "2026-08-06T12:40:00Z/2026-08-06T12:50:00Z",
                "links": {"data": [{"href": "https://api.eumetsat.int/data/download/slot1.nc"}]},
            },
        },
        {  # entrée sans lien → ignorée (tolérant)
            "id": "MTGFCI-0682-sans-lien",
            "properties": {"date": "2026-08-06T12:30:00Z/2026-08-06T12:40:00Z", "links": {}},
        },
    ]
}


@pytest.fixture()
def config():
    return load_config("config/params.toml")


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("EUMETSAT_CONSUMER_KEY", "k")
    monkeypatch.setenv("EUMETSAT_CONSUMER_SECRET", "s")


# --- identifiants -------------------------------------------------------------

def test_credentials_absents_leve(monkeypatch, config):
    monkeypatch.delenv("EUMETSAT_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("EUMETSAT_CONSUMER_SECRET", raising=False)
    with pytest.raises(eumetsat.EumetsatError):
        eumetsat.EumetsatClient(config).token()


# --- fonctions pures (format) -------------------------------------------------

def test_build_search_params(config):
    p = eumetsat.build_search_params(config, SINCE, UNTIL)
    assert p["pi"] == "EO:EUM:DAT:0682"
    assert p["dtstart"] == "2026-08-06T12:00:00Z"
    assert p["dtend"] == "2026-08-06T13:00:00Z"
    assert p["bbox"] == config["mtg"]["bbox"]


def test_parse_product_list_trie_et_tolerant():
    produits = eumetsat.parse_product_list(SEARCH_PAYLOAD)
    # l'entrée sans lien est écartée ; les deux autres sont triées par sensing_at croissant.
    assert [p["product_id"] for p in produits] == ["MTGFCI-0682-A-slot1", "MTGFCI-0682-B-slot2"]
    assert produits[0]["sensing_at"] == "2026-08-06T12:40:00Z"
    assert produits[0]["download_url"].endswith("slot1.nc")


def test_parse_product_list_vide():
    assert eumetsat.parse_product_list({}) == []


# --- token : cache & rafraîchissement -----------------------------------------

def test_token_mis_en_cache(monkeypatch, config):
    appels = {"n": 0}

    def fake_token(cfg, key, secret):
        appels["n"] += 1
        return {"access_token": f"tok{appels['n']}", "expires_in": 3600}

    monkeypatch.setattr(eumetsat, "_token_request", fake_token)
    client = eumetsat.EumetsatClient(config)
    assert client.token() == "tok1"
    assert client.token() == "tok1"  # 2e appel dans le TTL → pas de nouvelle requête
    assert appels["n"] == 1


def test_token_rafraichi_apres_expiration(monkeypatch, config):
    appels = {"n": 0}

    def fake_token(cfg, key, secret):
        appels["n"] += 1
        return {"access_token": f"tok{appels['n']}", "expires_in": 100}

    horloge = {"t": 1000.0}
    monkeypatch.setattr(eumetsat, "_token_request", fake_token)
    monkeypatch.setattr(eumetsat.time, "monotonic", lambda: horloge["t"])
    client = eumetsat.EumetsatClient(config)
    assert client.token() == "tok1"          # expire à 1000 + (100 - 60) = 1040
    horloge["t"] = 1039.0
    assert client.token() == "tok1"          # encore valide
    horloge["t"] = 1041.0
    assert client.token() == "tok2"          # expiré → refetch
    assert appels["n"] == 2


def test_token_sans_access_token_leve(monkeypatch, config):
    monkeypatch.setattr(eumetsat, "_token_request", lambda *a, **k: {"expires_in": 3600})
    with pytest.raises(eumetsat.EumetsatError):
        eumetsat.EumetsatClient(config).token()


# --- bout-en-bout listing / téléchargement ------------------------------------

def test_list_products_bout_en_bout(monkeypatch, config):
    monkeypatch.setattr(eumetsat, "_token_request",
                        lambda *a, **k: {"access_token": "tok", "expires_in": 3600})
    vu = {}

    def fake_get_json(cfg, url, token, params=None):
        vu["url"], vu["token"], vu["params"] = url, token, params
        return SEARCH_PAYLOAD

    monkeypatch.setattr(eumetsat, "_get_json", fake_get_json)
    produits = eumetsat.EumetsatClient(config).list_products(SINCE, UNTIL)
    assert len(produits) == 2
    assert vu["token"] == "tok"
    assert vu["url"].endswith("/search-products/1.0.0/os")
    assert vu["params"]["pi"] == "EO:EUM:DAT:0682"


def test_download_bytes(monkeypatch, config):
    monkeypatch.setattr(eumetsat, "_token_request",
                        lambda *a, **k: {"access_token": "tok", "expires_in": 3600})
    monkeypatch.setattr(eumetsat, "_get_bytes",
                        lambda cfg, url, token: b"\x89HDF\r\n\x1a\n netcdf bytes")
    data = eumetsat.EumetsatClient(config).download("https://api.eumetsat.int/data/download/slot1.nc")
    assert data.startswith(b"\x89HDF")
