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

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import UTC, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from vigifeu.engine.regen import enqueue
from vigifeu.generate.publish import origin_commune


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

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
# Mot-clé du feu (commune principale) — Spec 09 §3                             #
# --------------------------------------------------------------------------- #

def commune_principale(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    """Commune principale d'un feu pour le mot-clé presse (Spec 09 §3).

    Réutilise `publish.origin_commune` (commune d'origine = première détection ; même notion
    que le titre et le public_id du feu). À défaut d'emprise, la commune de proximité courante
    la plus proche. None si aucune commune fiable (→ pas d'appel). Retourne une ligne avec au
    moins `nom`, `code_insee`, `slug`.
    """
    c = origin_commune(conn, event_id)
    if c is not None:
        return c
    return conn.execute(
        "SELECT c.code_insee, c.slug, c.nom FROM fe_commune_rel r "
        "JOIN commune c ON c.code_insee = r.code_insee "
        "WHERE r.fire_event_id=? AND r.rel_type LIKE 'a_moins_de_%' AND r.valid_to IS NULL "
        "ORDER BY r.distance_km, c.code_insee LIMIT 1",
        (event_id,),
    ).fetchone()


def mots_cles_pour_feu(conn: sqlite3.Connection, config: dict, event_id: int) -> str | None:
    """« {prefixe} {commune} » (Spec 09 §3), ex. « incendie Saumos ». None si aucune commune
    fiable → le job n'appelle pas l'API pour ce feu (consigné, pas d'erreur)."""
    c = commune_principale(conn, event_id)
    if c is None:
        return None
    return f"{config['bulletins']['mot_cle_prefixe']} {c['nom']}"


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


# --------------------------------------------------------------------------- #
# Orchestration du cycle quotidien — Spec 09 §4                                #
# --------------------------------------------------------------------------- #

def _dates(config: dict, clock: datetime | None) -> tuple[str, str]:
    """(date_bulletin `YYYY-MM-DD`, date_jour `JJ/MM/AAAA`) en heure locale (Europe/Paris)."""
    tz = ZoneInfo(config["bulletins"]["timezone"])
    local = (clock or datetime.now(UTC)).astimezone(tz)
    return local.strftime("%Y-%m-%d"), local.strftime("%d/%m/%Y")


def _feux_actifs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Feux actifs végétation, du plus fort FRP au plus faible (priorité sous quota)."""
    return conn.execute(
        "SELECT fe.id, "
        "  (SELECT v.frp_total_last_pass_mw FROM fire_event_version v "
        "   WHERE v.fire_event_id=fe.id ORDER BY v.version_n DESC LIMIT 1) AS frp "
        "FROM fire_event fe "
        "WHERE fe.lifecycle='actif' AND fe.qualification='vegetation_confirme' "
        "ORDER BY frp DESC, fe.id"  # SQLite : NULL trié en dernier en DESC
    ).fetchall()


def _appel(mots_cles: str, date_jour: str, config: dict) -> tuple[str, object]:
    """Exécuté dans un thread : RÉSEAU SEUL (jamais la connexion SQLite). ('ok', parsed)
    ou ('error', message). Ne lève pas — l'écriture reste sérielle côté worker."""
    try:
        return ("ok", fetch_bulletin(mots_cles, date_jour, config))
    except Exception as exc:  # noqa: BLE001 — BulletinError ou réseau inattendu, journalisé
        return ("error", f"{type(exc).__name__}: {exc}")


def generer_bulletins(conn: sqlite3.Connection, config: dict, *, clock: datetime | None = None) -> dict:
    """Cycle quotidien (Spec 09 §4). Sélectionne les feux actifs, appelle l'API en fan-out
    concurrent (réseau seul), écrit en SÉRIE (écrivain SQLite unique préservé), consigne tout
    dans ingestion_run, enfile la regen des feux touchés. NE LÈVE JAMAIS (dégradé, Spec 02 §9).

    Retourne un dict de stats. `clock` (datetime UTC) injectable pour les tests.
    """
    b = config["bulletins"]
    date_bulletin, date_jour = _dates(config, clock)
    stats = {
        "date_bulletin": date_bulletin, "actifs": 0, "deja_presents": 0,
        "non_traites": 0, "sans_commune": 0, "appels": 0, "inseres": 0,
        "vides": 0, "erreurs": 0,
    }
    run_id = conn.execute(
        "INSERT INTO ingestion_run (source, params, started_at) VALUES ('bulletins', ?, ?)",
        (json.dumps({"date_jour": date_jour}), _now_iso()),
    ).lastrowid
    conn.commit()

    try:
        if not b["activated"]:
            _finir_run(conn, run_id, "ok", stats, note="désactivé (activated=false)")
            return stats

        actifs = _feux_actifs(conn)
        stats["actifs"] = len(actifs)
        deja = {
            r["fire_event_id"] for r in conn.execute(
                "SELECT fire_event_id FROM bulletin WHERE date_bulletin=?", (date_bulletin,)
            )
        }
        a_traiter = [f for f in actifs if f["id"] not in deja]
        stats["deja_presents"] = len(actifs) - len(a_traiter)
        if len(a_traiter) > b["max_feux_par_jour"]:
            stats["non_traites"] = len(a_traiter) - b["max_feux_par_jour"]
            a_traiter = a_traiter[: b["max_feux_par_jour"]]

        # Mot-clé par feu (touche la DB → dans le worker, AVANT le fan-out).
        travail: list[tuple[int, str]] = []
        for f in a_traiter:
            mc = mots_cles_pour_feu(conn, config, f["id"])
            if mc is None:
                stats["sans_commune"] += 1
            else:
                travail.append((f["id"], mc))
        stats["appels"] = len(travail)

        # Fan-out RÉSEAU concurrent (threads) ; les résultats reviennent au worker.
        resultats: dict[int, tuple[str, object]] = {}
        if travail:
            ex = ThreadPoolExecutor(max_workers=b["concurrence"])
            futures = {ex.submit(_appel, mc, date_jour, config): fid for fid, mc in travail}
            try:
                for fut in as_completed(futures, timeout=b["timeout_job_s"]):
                    resultats[futures[fut]] = fut.result()
            except FuturesTimeout:
                pass  # traînards : comptés en erreurs (non écrits) ci-dessous
            finally:
                ex.shutdown(wait=False, cancel_futures=True)

        # Écriture SÉRIELLE (worker unique).
        acq_at = _now_iso()
        mots = dict(travail)
        touches: list[int] = []
        for fid, _mc in travail:
            issue = resultats.get(fid)
            if issue is None or issue[0] == "error":
                stats["erreurs"] += 1
                continue
            parsed = issue[1]
            if est_vide(parsed):
                stats["vides"] += 1
                continue
            if _inserer_bulletin(conn, fid, date_bulletin, mots[fid], parsed,
                                 provider=b["provider"], acq_at=acq_at):
                stats["inseres"] += 1
                touches.append(fid)

        for fid in touches:
            enqueue(conn, "feu", str(fid), stamp=acq_at, trigger="bulletins")
        conn.commit()
        _finir_run(conn, run_id, "ok", stats)
        return stats
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        _finir_run(conn, run_id, "error", stats, note=f"{type(exc).__name__}: {exc}")
        return stats


def _inserer_bulletin(conn, fire_id, date_bulletin, mots_cles, parsed, *, provider, acq_at) -> bool:
    """Insère un bulletin non vide (idempotent via UNIQUE). True si inséré, False si déjà présent."""
    try:
        conn.execute(
            "INSERT INTO bulletin (fire_event_id, date_bulletin, mots_cles, resume, "
            "indicateurs_json, sources_json, articles_valides, fournisseurs_ia, provider, "
            "acq_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fire_id, date_bulletin, mots_cles, parsed["resume"],
                json.dumps(parsed["indicateurs"], ensure_ascii=False),
                json.dumps(parsed["sources"], ensure_ascii=False),
                parsed["articles_valides"],
                json.dumps(parsed["fournisseurs_ia"], ensure_ascii=False)
                if parsed["fournisseurs_ia"] is not None else None,
                provider, acq_at, acq_at,
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False  # déjà un bulletin pour (feu, jour) — rejeu, no-op (P1)


def _finir_run(conn, run_id: int, status: str, stats: dict, *, note: str | None = None) -> None:
    """Clôt l'ingestion_run avec le résumé du cycle (observabilité — Spec 01 §3.7)."""
    detail = {k: v for k, v in stats.items() if k != "date_bulletin"}
    if note:
        detail["note"] = note
    a_signaler = note or any(
        stats[k] for k in ("erreurs", "vides", "sans_commune", "non_traites")
    )
    conn.execute(
        "UPDATE ingestion_run SET finished_at=?, status=?, n_rows=?, n_new=?, error_text=? WHERE id=?",
        (
            _now_iso(), status, stats["appels"], stats["inseres"],
            json.dumps(detail, ensure_ascii=False) if a_signaler else None, run_id,
        ),
    )
    conn.commit()
