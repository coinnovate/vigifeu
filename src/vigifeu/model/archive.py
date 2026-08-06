"""Archivage Parquet et purge de la fenêtre glissante (Spec 01 §6, Spec 02 §2).

SQLite porte l'état vivant (fenêtre glissante) ; Parquet porte l'archive intégrale,
partitionnée `annee=/mois=/jour=`. `archive_sweep` (quotidien, nuit) :

1. exporte en Parquet les jours clos de hotspot_raw (idempotent : réécrit la partition) ;
2. purge de SQLite les hotspots hors fenêtre **déjà archivés** et non rattachés à un
   feu non archivé (règle cardinale Spec 01 §6) ;
3. archive puis purge le journal ingestion_run au-delà de sa rétention.

La purge n'est pas une suppression au sens P1 : le fait persiste dans l'archive.
Deux garde-fous rendent la perte impossible : on ne purge un jour que si sa
partition Parquet existe, et jamais un hotspot d'un feu encore vivant.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Colonnes exportées (schéma hotspot_raw complet — archive autonome).
_HOTSPOT_COLS = [
    "id", "source_id", "lat", "lon", "acq_at", "ingested_at", "ingestion_run_id",
    "frp_mw", "confidence", "scan_km", "track_km", "day_night", "raw_payload",
    "overpass_id", "fixed_source_id",
]

# geo_detection_raw (MTG, Spec 07 §4.3) — même discipline que hotspot_raw : export Parquet puis purge.
_GEODET_COLS = [
    "id", "provider", "lat", "lon", "acq_at", "ingested_at", "ingestion_run_id",
    "frp_mw", "frp_uncertainty_mw", "confidence", "quality_flag",
    "geo_candidate_id", "confirmed_by_fire_event_id", "raw_payload",
]


def _partition_path(root: Path, table: str, day_iso: str) -> Path:
    y, m, d = day_iso.split("-")
    return root / table / f"annee={y}" / f"mois={m}" / f"jour={d}" / f"{table}.parquet"


def _write_parquet(rows: list[sqlite3.Row], cols: list[str], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    data = {c: [r[c] for r in rows] for c in cols}
    pq.write_table(pa.table(data), path)


def export_hotspots_day(conn: sqlite3.Connection, config: dict, day_iso: str) -> tuple[Path, int]:
    """Exporte tous les hotspots d'un jour (par acq_at) vers sa partition Parquet.

    Idempotent : réécrit la partition. Retourne (chemin, nombre de lignes).
    """
    root = Path(config["archive"]["dir"])
    rows = conn.execute(
        f"SELECT {', '.join(_HOTSPOT_COLS)} FROM hotspot_raw "
        "WHERE substr(acq_at, 1, 10) = ? ORDER BY id",
        (day_iso,),
    ).fetchall()
    path = _partition_path(root, "hotspot_raw", day_iso)
    _write_parquet(rows, _HOTSPOT_COLS, path)
    return path, len(rows)


def _closed_days(conn: sqlite3.Connection, today: date) -> list[str]:
    """Jours d'acquisition entièrement passés (day < today) présents en base."""
    return [
        r["d"]
        for r in conn.execute(
            "SELECT DISTINCT substr(acq_at, 1, 10) AS d FROM hotspot_raw "
            "WHERE substr(acq_at, 1, 10) < ? ORDER BY d",
            (today.isoformat(),),
        )
    ]


def archive_sweep(
    conn: sqlite3.Connection, config: dict, today: date | None = None
) -> dict:
    """Passe quotidienne : exporte les jours clos, purge la fenêtre glissante."""
    today = today or datetime.now(UTC).date()
    root = Path(config["archive"]["dir"])
    ret = config["archive"]["hotspot_retention_days"]
    cutoff = today - timedelta(days=ret)

    days = _closed_days(conn, today)

    exported = 0
    for day_iso in days:
        _, n = export_hotspots_day(conn, config, day_iso)
        exported += n

    purged = 0
    protected = 0
    for day_iso in days:
        if date.fromisoformat(day_iso) > cutoff:
            continue  # encore dans la fenêtre vivante
        if not _partition_path(root, "hotspot_raw", day_iso).exists():
            continue  # garde-fou : jamais purger un jour non archivé
        # Hotspots du jour rattachés à un feu NON archivé : à conserver.
        protected += conn.execute(
            """SELECT COUNT(*) AS n FROM hotspot_raw h
               WHERE substr(h.acq_at, 1, 10) = ?
                 AND h.id IN (
                   SELECT fh.hotspot_id FROM fe_hotspot fh
                   JOIN fire_event_version fev ON fev.id = fh.fire_event_version_id
                   JOIN fire_event fe ON fe.id = fev.fire_event_id
                   WHERE fe.lifecycle != 'archive')""",
            (day_iso,),
        ).fetchone()["n"]
        cur = conn.execute(
            """DELETE FROM hotspot_raw
               WHERE substr(acq_at, 1, 10) = ?
                 AND id NOT IN (
                   SELECT fh.hotspot_id FROM fe_hotspot fh
                   JOIN fire_event_version fev ON fev.id = fh.fire_event_version_id
                   JOIN fire_event fe ON fe.id = fev.fire_event_id
                   WHERE fe.lifecycle != 'archive')""",
            (day_iso,),
        )
        purged += cur.rowcount

    geo = _sweep_geodetections(conn, config, today, root)
    ing = _sweep_ingestion_runs(conn, config, today)
    conn.commit()

    return {
        "exported_hotspots": exported,
        "purged_hotspots": purged,
        "protected_hotspots": protected,
        "exported_geodetections": geo["exported"],
        "purged_geodetections": geo["purged"],
        "protected_geodetections": geo["protected"],
        "purged_runs": ing,
    }


def _sweep_geodetections(conn: sqlite3.Connection, config: dict, today: date, root: Path) -> dict:
    """Archive Parquet + purge de geo_detection_raw (Spec 07 §4.3), même règle cardinale que hotspot :
    jamais une détection rattachée à un feu NON archivé, ni à un candidat encore `en_attente`."""
    ret = config["archive"].get("geo_detection_retention_days", 14)
    cutoff = today - timedelta(days=ret)
    days = [
        r["d"]
        for r in conn.execute(
            "SELECT DISTINCT substr(acq_at, 1, 10) AS d FROM geo_detection_raw "
            "WHERE substr(acq_at, 1, 10) < ? ORDER BY d",
            (today.isoformat(),),
        )
    ]
    exported = purged = protected = 0
    # Détections PROTÉGÉES : rattachées à un feu vivant, ou à un candidat en attente.
    protege = (
        "id IN (SELECT g.id FROM geo_detection_raw g "
        "  LEFT JOIN fire_event fe ON fe.id = g.confirmed_by_fire_event_id "
        "  LEFT JOIN geo_candidate gc ON gc.id = g.geo_candidate_id "
        "  WHERE (fe.id IS NOT NULL AND fe.lifecycle != 'archive') "
        "     OR (gc.id IS NOT NULL AND gc.status = 'en_attente'))"
    )
    for day_iso in days:
        rows = conn.execute(
            f"SELECT {', '.join(_GEODET_COLS)} FROM geo_detection_raw "
            "WHERE substr(acq_at, 1, 10) = ? ORDER BY id",
            (day_iso,),
        ).fetchall()
        _write_parquet(rows, _GEODET_COLS, _partition_path(root, "geo_detection_raw", day_iso))
        exported += len(rows)
    for day_iso in days:
        if date.fromisoformat(day_iso) > cutoff:
            continue  # encore dans la fenêtre vivante
        if not _partition_path(root, "geo_detection_raw", day_iso).exists():
            continue  # garde-fou : jamais purger un jour non archivé
        protected += conn.execute(
            f"SELECT COUNT(*) AS n FROM geo_detection_raw WHERE substr(acq_at, 1, 10) = ? AND {protege}",
            (day_iso,),
        ).fetchone()["n"]
        cur = conn.execute(
            f"DELETE FROM geo_detection_raw WHERE substr(acq_at, 1, 10) = ? AND NOT {protege}",
            (day_iso,),
        )
        purged += cur.rowcount
    return {"exported": exported, "purged": purged, "protected": protected}


def _sweep_ingestion_runs(conn: sqlite3.Connection, config: dict, today: date) -> int:
    """Archive (partition mensuelle) puis purge le journal au-delà de sa rétention."""
    ret = config["archive"].get("ingestion_run_retention_days", 90)
    cutoff = (today - timedelta(days=ret)).isoformat()
    old = conn.execute(
        "SELECT * FROM ingestion_run WHERE substr(started_at, 1, 10) < ?", (cutoff,)
    ).fetchall()
    if not old:
        return 0
    root = Path(config["archive"]["dir"])
    cols = list(old[0].keys())
    # Regroupement par mois d'started_at.
    by_month: dict[str, list] = {}
    for r in old:
        by_month.setdefault(r["started_at"][:7], []).append(r)  # YYYY-MM
    for month, rows in by_month.items():
        y, m = month.split("-")
        path = root / "ingestion_run" / f"annee={y}" / f"mois={m}" / "ingestion_run.parquet"
        _write_parquet(rows, cols, path)
    conn.execute("DELETE FROM ingestion_run WHERE substr(started_at, 1, 10) < ?", (cutoff,))
    return len(old)
