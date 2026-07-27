"""Ingestion FIRMS (Lot 0).

Exigences (Spec 02 §9, cadrage §5.1) :
- requêtes jour par jour, par satellite actif ;
- timeouts généreux, retries avec backoff (FIRMS ralentit les jours de grands feux) ;
- idempotence par clé d'unicité (réingérer un jour connu = zéro nouvelle ligne) ;
- chaque appel journalisé dans ingestion_run (la boîte noire) ;
- ingested_at posé à l'insertion : c'est LA donnée qui ne se rejoue pas (mesure de latence).

La MAP_KEY vient de l'environnement (FIRMS_MAP_KEY), jamais du repo.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class FirmsError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _acq_iso(acq_date: str, acq_time: str) -> str:
    """FIRMS fournit acq_date=YYYY-MM-DD et acq_time=HHMM (UTC) → ISO UTC."""
    t = acq_time.zfill(4)
    return f"{acq_date}T{t[:2]}:{t[2:]}:00Z"


def build_url(config: dict, source_code: str, day: date) -> str:
    f = config["firms"]
    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not map_key:
        raise FirmsError("FIRMS_MAP_KEY absente de l'environnement (cf. .env.example)")
    bbox = config["general"]["firms_bbox"]
    return (
        f"{f['base_url']}/{map_key}/{source_code}/{bbox}/"
        f"{f['day_range']}/{day.isoformat()}"
    )


def _fetch_csv(url: str, timeout_s: float, max_retries: int, wait_min: float, wait_max: float) -> httpx.Response:
    """GET avec retries/backoff. Les erreurs réseau et 5xx/429 sont réessayées."""

    @retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(min=wait_min, max=wait_max),
        retry=retry_if_exception_type((httpx.TransportError, FirmsError)),
        reraise=True,
    )
    def _do() -> httpx.Response:
        resp = httpx.get(url, timeout=timeout_s, follow_redirects=True)
        if resp.status_code in (429, 500, 502, 503, 504):
            raise FirmsError(f"HTTP {resp.status_code} (réessayable)")
        return resp

    return _do()


def ingest_day(
    conn: sqlite3.Connection,
    config: dict,
    source_row: sqlite3.Row,
    day: date,
) -> dict:
    """Ingère un (satellite, jour). Retourne un résumé {status, n_rows, n_new}.

    Ne lève jamais vers l'appelant pour une erreur de source : l'échec est
    journalisé et le cycle continue (Spec 02 P3 — jamais bloquant sur une source).
    """
    f = config["firms"]
    run_id = conn.execute(
        "INSERT INTO ingestion_run (source, params, started_at) VALUES (?, ?, ?)",
        (
            f"firms:{source_row['code']}",
            json.dumps({"day": day.isoformat(), "bbox": config["general"]["firms_bbox"]}),
            _now_iso(),
        ),
    ).lastrowid
    conn.commit()

    try:
        url = build_url(config, source_row["code"], day)
        resp = _fetch_csv(
            url, f["timeout_s"], f["max_retries"], f["retry_wait_min_s"], f["retry_wait_max_s"]
        )
        if resp.status_code != 200:
            raise FirmsError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.text
        if body.startswith("Invalid"):  # FIRMS répond 200 avec un message d'erreur texte
            raise FirmsError(body[:200])

        n_rows, n_new = _insert_rows(conn, source_row["id"], run_id, body)
        conn.execute(
            """UPDATE ingestion_run
               SET finished_at=?, status='ok', http_status=?, n_rows=?, n_new=?
               WHERE id=?""",
            (_now_iso(), resp.status_code, n_rows, n_new, run_id),
        )
        conn.commit()
        return {"status": "ok", "n_rows": n_rows, "n_new": n_new}

    except Exception as exc:  # noqa: BLE001 — journalisé, jamais silencieux
        conn.execute(
            """UPDATE ingestion_run
               SET finished_at=?, status='error', error_text=? WHERE id=?""",
            (_now_iso(), f"{type(exc).__name__}: {exc}", run_id),
        )
        conn.commit()
        return {"status": "error", "error": str(exc)}


def _insert_rows(
    conn: sqlite3.Connection, source_id: int, run_id: int, csv_body: str
) -> tuple[int, int]:
    """Insertion idempotente (INSERT OR IGNORE sur la clé d'unicité).

    ingested_at n'est posé QUE pour les lignes nouvelles — une ligne déjà connue
    garde son ingested_at d'origine (P3 : la latence mesurée ne se réécrit pas).
    """
    reader = csv.DictReader(io.StringIO(csv_body))
    n_rows = 0
    n_new = 0
    now = _now_iso()
    for row in reader:
        n_rows += 1
        cur = conn.execute(
            """INSERT OR IGNORE INTO hotspot_raw
               (source_id, lat, lon, acq_at, ingested_at, ingestion_run_id,
                frp_mw, confidence, scan_km, track_km, day_night, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                float(row["latitude"]),
                float(row["longitude"]),
                _acq_iso(row["acq_date"], row["acq_time"]),
                now,
                run_id,
                float(row["frp"]) if row.get("frp") else None,
                row.get("confidence"),
                float(row["scan"]) if row.get("scan") else None,
                float(row["track"]) if row.get("track") else None,
                row.get("daynight"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        n_new += cur.rowcount
    return n_rows, n_new


def fetch_cycle(conn: sqlite3.Connection, config: dict) -> list[dict]:
    """Un cycle complet : jour courant (+ J-1 si configuré) pour chaque satellite actif."""
    from vigifeu.model.db import active_sources

    today = datetime.now(UTC).date()
    days = [today]
    if config["firms"].get("check_previous_day", True):
        days.append(today - timedelta(days=1))

    results = []
    for src in active_sources(conn):
        for day in days:
            summary = ingest_day(conn, config, src, day)
            summary.update({"source": src["code"], "day": day.isoformat()})
            results.append(summary)
    return results


def days_needing_backfill(
    conn: sqlite3.Connection, config: dict, today: date
) -> list[tuple[sqlite3.Row, date]]:
    """(satellite, jour) sans run FIRMS réussi dans la fenêtre [today-N, today].

    Un jour déjà ingéré avec succès n'est jamais un trou : on possède déjà sa
    donnée, un échec ultérieur de re-fetch ne la perd pas. Un jour avec seulement
    des runs en échec — ou jamais tenté (panne du daemon) — est un trou.
    """
    from vigifeu.model.db import active_sources

    n = config["firms"].get("backfill_days", 7)
    days = [today - timedelta(days=d) for d in range(n + 1)]
    gaps: list[tuple[sqlite3.Row, date]] = []
    for src in active_sources(conn):
        for day in days:
            ok = conn.execute(
                """SELECT 1 FROM ingestion_run
                   WHERE source=? AND status='ok'
                     AND json_extract(params, '$.day')=? LIMIT 1""",
                (f"firms:{src['code']}", day.isoformat()),
            ).fetchone()
            if not ok:
                gaps.append((src, day))
    return gaps


def fetch_firms_backfill(
    conn: sqlite3.Connection, config: dict, today: date | None = None
) -> list[dict]:
    """Tâche de rattrapage (Spec 02 §2, horaire) : ré-ingère les jours à trous.

    Rejoue chaque (satellite, jour) sans succès dans la fenêtre. Idempotent :
    sans trou, ne fait rien. Le trou reste visible dans ingestion_run (les runs
    en échec ne sont pas effacés — la boîte noire garde la trace de la panne).
    """
    today = today or datetime.now(UTC).date()
    results = []
    for src, day in days_needing_backfill(conn, config, today):
        summary = ingest_day(conn, config, src, day)
        summary.update({"source": src["code"], "day": day.isoformat()})
        results.append(summary)
    return results
