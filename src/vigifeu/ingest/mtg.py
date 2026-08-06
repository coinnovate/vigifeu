"""Ingestion MTG FCI L2 « Active Fire Monitoring » (Spec 07 §2/§4, étape 4).

Assemble les trois briques : listing (`ingest/eumetsat.py`) → téléchargement → parsing
(`ingest/mtg_netcdf.py`) → insertion idempotente dans `geo_detection_raw`.

Exigences (comme `ingest/firms.py`) :
- rattrapage de TOUS les granules depuis le dernier succès (Spec 07 §2 : aucun slot 10 min perdu) ;
- idempotence par clé d'unicité (réingérer un slot connu = zéro nouvelle ligne) ;
- `ingested_at` posé À l'insertion, JAMAIS réécrit (mesure de latence, P3) ;
- chaque cycle journalisé dans `ingestion_run` (source `mtg:0682`) — la boîte noire ;
- jamais bloquant : un échec de source est journalisé, le cycle FIRMS n'est pas interrompu (Spec 02 P3).
  Un granule illisible est sauté (compté), le reste du cycle continue.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from vigifeu.ingest.eumetsat import EumetsatClient
from vigifeu.ingest.mtg_netcdf import parse_listproduct

_SOURCE = "mtg:0682"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _window_since(conn: sqlite3.Connection, config: dict, now: datetime) -> datetime:
    """Début de la fenêtre à interroger : le `until` du dernier run MTG réussi (rattrape tout depuis),
    sinon `now - catchup_hours` (borne du 1er rattrapage — pas de backlog infini au démarrage)."""
    row = conn.execute(
        "SELECT params FROM ingestion_run WHERE source=? AND status='ok' AND params IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (_SOURCE,),
    ).fetchone()
    if row:
        try:
            until = json.loads(row["params"]).get("until")
            if until:
                return _parse_iso(until)
        except (ValueError, TypeError):
            pass
    return now - timedelta(hours=config["mtg"]["catchup_hours"])


def _insert_pixels(
    conn: sqlite3.Connection, config: dict, run_id: int, pixels: list[dict]
) -> tuple[int, int]:
    """Insertion idempotente (INSERT OR IGNORE sur (provider, acq_at, lat, lon)).

    `ingested_at` n'est posé QUE pour les lignes nouvelles — une ligne déjà connue garde son
    `ingested_at` d'origine (la latence mesurée ne se réécrit pas, P3).
    """
    provider = config["mtg"]["provider"]
    now = _now_iso()
    n_rows = n_new = 0
    for px in pixels:
        if px.get("acq_at") is None:  # sans horodatage phénomène, on ne peut pas mesurer la latence
            continue
        n_rows += 1
        cur = conn.execute(
            """INSERT OR IGNORE INTO geo_detection_raw
               (provider, lat, lon, acq_at, ingested_at, ingestion_run_id,
                frp_mw, frp_uncertainty_mw, confidence, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider,
                px["lat"],
                px["lon"],
                px["acq_at"],
                now,
                run_id,
                px.get("frp_mw"),
                px.get("frp_uncertainty_mw"),
                px.get("confidence"),
                json.dumps(px, ensure_ascii=False),
            ),
        )
        n_new += cur.rowcount
    return n_rows, n_new


def fetch_mtg_fir(
    conn: sqlite3.Connection,
    config: dict,
    *,
    client: EumetsatClient | None = None,
    clock: datetime | None = None,
) -> dict:
    """Un cycle d'ingestion MTG. Retourne un résumé {status, n_products, n_rows, n_new, n_err}.

    Ne lève jamais vers l'appelant pour une erreur de source : l'échec est journalisé dans
    `ingestion_run` et le cycle rend la main (Spec 02 P3). `client`/`clock` injectables (tests).
    """
    m = config["mtg"]
    now = clock or datetime.now(UTC)
    since = _window_since(conn, config, now)

    run_id = conn.execute(
        "INSERT INTO ingestion_run (source, params, started_at) VALUES (?, ?, ?)",
        (
            _SOURCE,
            json.dumps({"since": _iso(since), "until": _iso(now), "bbox": m["bbox"]}),
            _now_iso(),
        ),
    ).lastrowid
    conn.commit()

    stats = {"status": "ok", "n_products": 0, "n_rows": 0, "n_new": 0, "n_err": 0}
    try:
        if not m["activated"]:
            _finir(conn, run_id, "ok", stats, note="désactivé (activated=false)")
            return stats

        cli = client or EumetsatClient(config)
        produits = cli.list_products(since, now)
        stats["n_products"] = len(produits)

        for prod in produits:
            try:
                data = cli.download(prod["download_url"])
                pixels = parse_listproduct(
                    data, config, bbox=m["bbox"], default_acq_at=prod.get("sensing_at")
                )
                n_rows, n_new = _insert_pixels(conn, config, run_id, pixels)
                stats["n_rows"] += n_rows
                stats["n_new"] += n_new
            except Exception:  # noqa: BLE001 — granule illisible : sauté, compté, jamais bloquant
                stats["n_err"] += 1
        conn.commit()

        note = f"{stats['n_err']} granule(s) en échec" if stats["n_err"] else None
        _finir(conn, run_id, "ok", stats, note=note)
        return stats
    except Exception as exc:  # noqa: BLE001 — journalisé, jamais silencieux ni bloquant
        stats["status"] = "error"
        _finir(conn, run_id, "error", stats, note=f"{type(exc).__name__}: {exc}")
        return stats


def run_mtg_cycle(
    conn: sqlite3.Connection,
    config: dict,
    *,
    client: EumetsatClient | None = None,
    clock: datetime | None = None,
) -> dict:
    """Cycle MTG complet : ingestion → confirmation VIIRS → candidats (promotion/amorçage/expiration).

    Assemble les étapes 4-6 pour le daemon (étape 9). Retourne un résumé + `fires` (feux dont la
    fiche doit être régénérée) et `carte` (booléen : le calque « signaux en attente » a changé).
    Les imports moteur sont locaux pour éviter tout cycle d'import ingest ↔ engine.
    """
    from vigifeu.engine.geo_candidate import process_candidates
    from vigifeu.engine.geo_confirm import confirm_detections

    now = clock or datetime.now(UTC)
    fetch = fetch_mtg_fir(conn, config, client=client, clock=now)
    conf = confirm_detections(conn, config, clock=now)
    cand = process_candidates(conn, config, clock=now)
    fires = sorted(set(conf["fires"]) | set(cand["fires"]))
    carte = bool(
        fetch.get("n_new") or conf["n_confirmed"]
        or cand["promus"] or cand["crees"] or cand["grossis"] or cand["expires"]
    )
    return {"fetch": fetch, "confirm": conf, "candidates": cand, "fires": fires, "carte": carte}


def _finir(conn, run_id: int, status: str, stats: dict, *, note: str | None = None) -> None:
    """Clôt l'ingestion_run avec le résumé du cycle (observabilité — Spec 01 §3.7)."""
    detail = {k: v for k, v in stats.items() if k != "status"}
    if note:
        detail["note"] = note
    conn.execute(
        "UPDATE ingestion_run SET finished_at=?, status=?, n_rows=?, n_new=?, error_text=? WHERE id=?",
        (
            _now_iso(),
            status,
            stats["n_rows"],
            stats["n_new"],
            json.dumps(detail, ensure_ascii=False) if (note or stats["n_err"]) else None,
            run_id,
        ),
    )
    conn.commit()
