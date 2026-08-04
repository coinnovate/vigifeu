"""Client de l'API de veille presse `news.co-innovate.eu` (Spec 09).

L'API prend des mots-clés + une date + une liste d'indicateurs typés, scrape la presse,
consolide par IA et renvoie, par indicateur, {valeur, statut, sources} + un champ `resume`
(2-4 phrases, construit UNIQUEMENT sur les valeurs confirmées). Le `resume` EST le corps du
bulletin (Spec 09 §1). Traitement asynchrone : POST /recherches → 202 + `id_tache`, puis
GET /recherches/{id_tache} jusqu'à `termine`/`erreur`.

Découpage (comme les autres fetchers) : le format de l'API n'est touché QUE par `parse_resultat`
et `build_request` — les deux fonctions PURES, testables hors réseau. Le réseau (POST + polling,
timeouts, backoff 429) vit dans `fetch_bulletin`, qui lève `BulletinError` ; l'orchestration
(Spec 09 §4, étape 4) l'attrape et journalise dans `ingestion_run`.

Contenu tiers : `resume`/indicateurs sont du texte d'un service externe → traités comme DONNÉE,
jamais comme instruction ; échappés au rendu (autoescape Jinja, étape 6). Règles juridiques dures :
Spec 09 §10 (faits seulement, liens pas extraits, comptes pas de noms, pas de photo).
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Seuls ces statuts d'indicateur portent une valeur exploitable (Spec 09 §1). `inconnu` =
# non confirmé, champ vide → écarté (« champs vides = honnêtes » du guide d'intégration).
_STATUTS_RETENUS = ("confirmé", "environ")


class BulletinError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Fonctions PURES (format de l'API) — testables sans réseau                    #
# --------------------------------------------------------------------------- #

def build_request(mots_cles: str, date_jour: str, config: dict) -> dict:
    """Corps JSON de `POST /recherches`. `date_jour` est déjà au format JJ/MM/AAAA
    (date Europe/Paris, construite par le job — Spec 09 §5)."""
    b = config["bulletins"]
    return {
        "mots_cles": mots_cles,
        "date_jour": date_jour,
        "nb_articles": b["nb_articles"],
        "fenetre_jours": b["fenetre_jours"],
        "min_sources": b["min_sources"],
        "langue": b["langue"],
        "pays": b["pays"],
        "indicateurs": b["indicateurs"],
    }


def _hote(url: object) -> str | None:
    """Hôte d'affichage d'une URL source (Spec 09 §5 : on montre l'hôte, pas l'URL brute).
    Filtre les valeurs non-http(s) (Spec 09 §6 : URLs validées avant rendu)."""
    if not isinstance(url, str):
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return p.hostname[4:] if p.hostname.startswith("www.") else p.hostname


def parse_resultat(resultat: dict) -> dict:
    """Normalise le `resultat` de l'API en structure prête pour le stockage/affichage.

    Retourne {resume, indicateurs, sources, articles_valides, fournisseurs_ia} :
      - `resume` : corps du bulletin (str, éventuellement vide) ;
      - `indicateurs` : seulement `confirmé`/`environ` porteurs d'une valeur ({indicateur,
        valeur, statut}) — les `inconnu` et valeurs vides écartés ;
      - `sources` : URLs distinctes (http/https) citées par ces indicateurs, {url, hote},
        dédupliquées et dans l'ordre d'apparition.
    SEULE (avec build_request) fonction dépendante du format ; point de vérification live.
    """
    resume = (resultat.get("resume") or "").strip()
    indicateurs: list[dict] = []
    sources: list[dict] = []
    vus: set[str] = set()
    for ind in resultat.get("indicateurs") or []:
        if ind.get("statut") not in _STATUTS_RETENUS:
            continue
        valeur = (ind.get("valeur") or "").strip()
        if not valeur:
            continue
        indicateurs.append(
            {"indicateur": ind.get("indicateur"), "valeur": valeur, "statut": ind.get("statut")}
        )
        for url in ind.get("sources") or []:
            hote = _hote(url)
            if hote and url not in vus:
                vus.add(url)
                sources.append({"url": url, "hote": hote})
    return {
        "resume": resume,
        "indicateurs": indicateurs,
        "sources": sources,
        "articles_valides": resultat.get("articles_valides"),
        "fournisseurs_ia": resultat.get("fournisseurs_ia"),
    }


def est_vide(parsed: dict) -> bool:
    """Bulletin sans matière : pas de résumé ET aucun indicateur retenu. Le job ne crée
    alors pas de ligne `bulletin` (Spec 09 §2), il consigne l'absence dans ingestion_run."""
    return not parsed["resume"] and not parsed["indicateurs"]


# --------------------------------------------------------------------------- #
# Réseau (POST + polling) — isolé du parsing ; lève BulletinError              #
# --------------------------------------------------------------------------- #

_HTTP_REESSAYABLE = (429, 500, 502, 503, 504)


def _post_recherche(base_url: str, body: dict, config: dict) -> str:
    """Lance la tâche asynchrone. Retourne `id_tache`. Retry borné sur 429/5xx."""
    b = config["bulletins"]

    @retry(
        stop=stop_after_attempt(b["max_retries"]),
        wait=wait_exponential(min=b["retry_wait_min_s"], max=b["retry_wait_max_s"]),
        retry=retry_if_exception_type((httpx.TransportError, BulletinError)),
        reraise=True,
    )
    def _do() -> str:
        resp = httpx.post(f"{base_url}/recherches", json=body, timeout=b["timeout_feu_s"])
        if resp.status_code in _HTTP_REESSAYABLE:
            raise BulletinError(f"HTTP {resp.status_code} (réessayable)")
        if resp.status_code not in (200, 202):
            raise BulletinError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        id_tache = resp.json().get("id_tache")
        if not id_tache:
            raise BulletinError("réponse de lancement sans id_tache")
        return id_tache

    return _do()


def _poll_resultat(base_url: str, id_tache: str, config: dict) -> dict:
    """Interroge la tâche jusqu'à `termine` (retourne `resultat`) ou `erreur`. Un 429/5xx
    pendant le polling n'abandonne pas : on repolle jusqu'au `timeout_feu_s`."""
    b = config["bulletins"]
    deadline = time.monotonic() + b["timeout_feu_s"]
    while True:
        if time.monotonic() > deadline:
            raise BulletinError("timeout : tâche non terminée")
        time.sleep(b["poll_intervalle_s"])
        resp = httpx.get(f"{base_url}/recherches/{id_tache}", timeout=b["timeout_feu_s"])
        if resp.status_code in _HTTP_REESSAYABLE:
            continue
        if resp.status_code != 200:
            raise BulletinError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        tache = resp.json()
        statut = tache.get("statut")
        if statut == "termine":
            return tache.get("resultat") or {}
        if statut == "erreur":
            raise BulletinError(f"tâche en erreur : {tache.get('erreur')}")
        # en_cours → on continue de poller


def fetch_bulletin(mots_cles: str, date_jour: str, config: dict) -> dict:
    """Flux complet pour un feu : POST asynchrone + polling + parsing. Retourne le dict
    de `parse_resultat`. Lève `BulletinError` en cas d'échec (l'orchestration journalise)."""
    base_url = config["bulletins"]["base_url"]
    body = build_request(mots_cles, date_jour, config)
    id_tache = _post_recherche(base_url, body, config)
    return parse_resultat(_poll_resultat(base_url, id_tache, config))
