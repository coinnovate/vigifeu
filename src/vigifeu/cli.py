"""CLI Vigifeu (Lot 0).

  vigifeu init                    — crée/migre la base, synchronise les sources
  vigifeu fetch [YYYY-MM-DD]      — ingère un jour (défaut : aujourd'hui) pour tous les satellites
  vigifeu backfill A B            — ingère l'intervalle de jours [A, B] (constitution de fixtures)
  vigifeu backfill-gaps           — rattrape les jours à trous (runs en échec) jusqu'à J-7
  vigifeu archive                 — export Parquet des jours clos + purge de la fenêtre glissante
  vigifeu latence                 — statistiques de latence NRT (le jalon L0)
  vigifeu runs [N]                — derniers runs d'ingestion (défaut : 20)
  vigifeu moteur                  — un cycle du moteur sur les nouveautés (clustering→versions)
  vigifeu rejeu                   — reconstruit toute l'interprétation depuis hotspot_raw (P2)
  vigifeu feux [N]                — derniers FireEvents (défaut : 30)
  vigifeu sources                 — sources fixes candidates en attente de revue
  vigifeu confirmer ID [type]     — confirme une source fixe candidate
  vigifeu invalider ID            — rejette une source fixe candidate
  vigifeu communes-import PATH [--millesime M] [--layer L]
                                  — importe le référentiel commune (GeoPackage/GeoJSON)
  vigifeu bdiff-import PATH [--replace]
                                  — importe l'historique BDIFF (CSV, cumulatif ; --replace efface d'abord)
  vigifeu poi-import PATH [--source osm|bdtopo|georisques]
                                  — importe une source du référentiel POI (défaut osm) + dédup inter-sources + recense par commune
  vigifeu contexte                — tire drought/vigieau des communes concernées (flags config)
  vigifeu generer [--limit N]     — régénère les pages en attente dans regen_queue (Lot 4)
  vigifeu rebuild                 — régénère TOUT le HTML (après un changement de gabarit)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

from vigifeu.ingest.firms import fetch_firms_backfill, ingest_day
from vigifeu.model.db import (
    active_sources,
    connect,
    load_config,
    migrate,
    sync_satellite_sources,
)


def _open():
    config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))
    conn = connect(config["general"]["db_path"])
    migrate(conn)
    sync_satellite_sources(conn, config)
    return conn, config


def cmd_init() -> None:
    conn, config = _open()
    n = conn.execute("SELECT COUNT(*) AS n FROM satellite_source WHERE active=1").fetchone()["n"]
    print(f"base prête : {config['general']['db_path']} — {n} sources actives")


def cmd_fetch(day_str: str | None) -> None:
    conn, config = _open()
    day = date.fromisoformat(day_str) if day_str else datetime.now(UTC).date()
    for src in active_sources(conn):
        r = ingest_day(conn, config, src, day)
        print(f"{src['code']:>18} {day}: {r}")


def cmd_backfill(start_str: str, end_str: str) -> None:
    conn, config = _open()
    day, end = date.fromisoformat(start_str), date.fromisoformat(end_str)
    while day <= end:
        for src in active_sources(conn):
            r = ingest_day(conn, config, src, day)
            print(f"{src['code']:>18} {day}: {r}")
        day += timedelta(days=1)


def cmd_backfill_gaps() -> None:
    conn, config = _open()
    results = fetch_firms_backfill(conn, config)
    if not results:
        print("aucun trou à rattraper")
        return
    for r in results:
        print(f"{r['source']:>18} {r['day']}: {r}")


def cmd_archive() -> None:
    from vigifeu.model.archive import archive_sweep

    conn, config = _open()
    res = archive_sweep(conn, config)
    print(
        f"archive : {res['exported_hotspots']} hotspots exportés, "
        f"{res['purged_hotspots']} purgés ({res['protected_hotspots']} protégés), "
        f"{res['purged_runs']} runs purgés"
    )


def cmd_latence() -> None:
    conn, _ = _open()
    rows = conn.execute(
        """SELECT source, COUNT(*) AS n,
                  ROUND(MIN(latence_h), 2) AS min_h,
                  ROUND(AVG(latence_h), 2) AS moy_h,
                  ROUND(MAX(latence_h), 2) AS max_h
           FROM v_latence_nrt GROUP BY source ORDER BY source"""
    ).fetchall()
    if not rows:
        print("aucun hotspot ingéré pour l'instant")
        return
    print(f"{'source':>18} {'n':>7} {'min (h)':>8} {'moy (h)':>8} {'max (h)':>8}")
    for r in rows:
        print(f"{r['source']:>18} {r['n']:>7} {r['min_h']:>8} {r['moy_h']:>8} {r['max_h']:>8}")
    print("\nnote : la latence n'est significative que pour les hotspots ingérés en continu")
    print("(un backfill d'archive mesure le backfill, pas le NRT)")


def cmd_runs(n: int) -> None:
    conn, _ = _open()
    for r in conn.execute(
        "SELECT * FROM ingestion_run ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall():
        print(
            f"#{r['id']} {r['started_at']} {r['source']:<24} {r['status']:<7}"
            f" rows={r['n_rows']} new={r['n_new']}"
            + (f" ERR: {r['error_text']}" if r["error_text"] else "")
        )


def cmd_moteur() -> None:
    from vigifeu.engine.pipeline import process_cycle

    conn, config = _open()
    res = process_cycle(conn, config)
    print(
        f"moteur : {res['created']} créés, {res['attached']} rattachés, "
        f"{res['merged']} fusions, {res['reprises']} reprises, "
        f"{res['requalified']} requalifiés, {res['versioned']} versions, "
        f"{res['promoted']} sources promues"
    )


def cmd_rejeu() -> None:
    from vigifeu.engine.pipeline import process_cycle, reset_interpretation

    conn, config = _open()
    n = conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]
    print(f"rejeu de {n} hotspots — remise à zéro de l'interprétation…")
    reset_interpretation(conn, config)
    res = process_cycle(conn, config)
    print(
        f"terminé : {res['created']} feux, {res['merged']} fusions, "
        f"{res['versioned']} versions, {res['promoted']} sources fixes candidates"
    )
    for r in conn.execute(
        "SELECT qualification, COUNT(*) AS n FROM fire_event "
        "GROUP BY qualification ORDER BY n DESC"
    ):
        print(f"  {r['qualification'] or '(non qualifié)':<22} {r['n']}")


def cmd_feux(n: int) -> None:
    conn, _ = _open()
    for r in conn.execute(
        "SELECT id, public_id, first_acq_at, last_acq_at, qualification, lifecycle "
        "FROM fire_event ORDER BY last_acq_at DESC LIMIT ?", (n,)
    ).fetchall():
        print(
            f"#{r['id']:<5} {r['first_acq_at']}→{r['last_acq_at']} "
            f"{(r['qualification'] or '—'):<22} {r['lifecycle']:<12} {r['public_id'] or ''}"
        )


def cmd_sources() -> None:
    from vigifeu.engine.fixed_source import list_candidates

    conn, _ = _open()
    rows = list_candidates(conn)
    if not rows:
        print("aucune source fixe candidate en attente")
        return
    for r in rows:
        print(f"#{r['id']:<4} {r['lat']:.4f},{r['lon']:.4f} r={r['radius_m']:.0f}m "
              f"{r['evidence_json']}")


def cmd_confirmer(source_id: int, kind: str | None) -> None:
    from vigifeu.engine.fixed_source import confirm_candidate

    conn, _ = _open()
    confirm_candidate(conn, source_id, stamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      kind=kind)
    print(f"source #{source_id} confirmée{f' ({kind})' if kind else ''}")


def cmd_invalider(source_id: int) -> None:
    from vigifeu.engine.fixed_source import invalidate_candidate

    conn, _ = _open()
    invalidate_candidate(conn, source_id, stamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print(f"source #{source_id} invalidée")


def _flag(rest: list[str], name: str) -> str | None:
    """Extrait --name=valeur ou --name valeur d'une liste d'arguments."""
    for i, a in enumerate(rest):
        if a == f"--{name}" and i + 1 < len(rest):
            return rest[i + 1]
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return None


def cmd_communes_import(path: str, millesime: str | None, layer: str | None) -> None:
    from vigifeu.referentiels.communes import import_communes

    conn, _ = _open()
    m = millesime or datetime.now(UTC).strftime("%Y")
    res = import_communes(conn, path, millesime=m, layer=layer)
    print(f"communes importées : {res['imported']} (millésime {res['millesime']})")


def cmd_bdiff_import(path: str, replace: bool) -> None:
    from vigifeu.referentiels.bdiff import import_bdiff

    conn, _ = _open()
    res = import_bdiff(conn, path, replace=replace)
    print(
        f"BDIFF : {res['imported']} feux importés sur {res['communes_touchees']} communes, "
        f"{res['duplicates_ignored']} déjà présents, "
        f"{res['skipped_unknown_commune']} ignorés (code INSEE hors référentiel)"
    )


def cmd_poi_import(path: str, source: str | None) -> None:
    """Importe une source du référentiel POI, puis déduplique et recense par commune.

    `--source osm` (défaut) / `bdtopo` / `georisques`. Chaque source s'importe
    indépendamment (upsert par clé naturelle) ; la dédup inter-sources et le recensement
    communal sont recalculés après chaque import (idempotents, Spec 06 §2.3/§3.2).
    """
    from vigifeu.engine.relations import recompute_commune_poi, recompute_poi_dedup

    src = (source or "osm").lower()
    conn, config = _open()
    if src == "osm":
        from vigifeu.referentiels.poi_osm import import_poi_osm
        res = import_poi_osm(conn, path, config)
    elif src == "bdtopo":
        from vigifeu.referentiels.poi_bdtopo import import_poi_bdtopo
        res = import_poi_bdtopo(conn, path, config)
    elif src == "georisques":
        from vigifeu.referentiels.poi_georisques import import_poi_georisques
        res = import_poi_georisques(conn, path, config)
        res.setdefault("by_category", {"icpe_seveso": res["upserted"]})
    else:
        print(f"source POI inconnue : {src} (osm/bdtopo/georisques)", file=sys.stderr)
        sys.exit(1)

    dedup = recompute_poi_dedup(conn, config)
    census = recompute_commune_poi(conn)
    cats = ", ".join(f"{k}={v}" for k, v in sorted(res["by_category"].items())) or "aucune"
    print(
        f"POI {src} : {res['upserted']} importés ({cats}), {res['skipped']} ignorés ; "
        f"dédup : {dedup['superseded']} doublons masqués ({dedup['canonical']} canoniques) ; "
        f"recensement commune : {census['pairs']} rattachements"
    )


def cmd_generer(limit: int | None) -> None:
    from vigifeu.generate.runner import consume, finalize_site, sync_static

    from vigifeu.generate.lint import lint_lexique, no_generation_timestamp

    conn, config = _open()
    sync_static(config)
    stamp = datetime.now(UTC).isoformat()
    stats = consume(conn, config, stamp=stamp, limit=limit)
    site = finalize_site(conn, config)
    print(
        f"généré : {stats['feu']} feux, {stats['commune']} communes, {stats['carte']} carte "
        f"— {stats['differe']} différées, {stats['erreurs']} erreurs. "
        f"Site : {site['pages']} pages, sitemaps {site['sitemaps']}, {site['redirects']} redirections "
        f"→ {config['generate']['site_dir']}"
    )
    # Garde-fous Spec 04 §9 (avertissement au build ; l'échec dur est en CI).
    site_dir = config["generate"]["site_dir"]
    for v in lint_lexique(site_dir):
        print(f"  ⚠ terme interdit : {v['terme']} dans {v['file']}", file=sys.stderr)
    for v in no_generation_timestamp(site_dir):
        print(f"  ⚠ horodatage de génération : {v['marqueur']} dans {v['file']}", file=sys.stderr)


def cmd_rebuild() -> None:
    """Régénère TOUT le HTML du site (après un changement de gabarit/lexique).

    Ré-enfile tous les feux publiés + communes du périmètre + carte, puis génère.
    Ne recopie pas les assets (CSS/logo/carte-config) : un changement de CSS s'applique
    sans régénération, et les assets sont rafraîchis par le déploiement (sync_static au
    boot, avec la clé MapTiler). À lancer daemon arrêté (écrivain unique)."""
    from vigifeu.engine.regen import CARTE_REF, enqueue
    from vigifeu.generate.perimetre import communes_indexables
    from vigifeu.generate.runner import consume, finalize_site

    conn, config = _open()
    stamp = datetime.now(UTC).isoformat()
    n = enqueue(conn, "carte", CARTE_REF, stamp=stamp, trigger="rebuild")
    for f in conn.execute("SELECT id FROM fire_event WHERE public_id IS NOT NULL"):
        n += enqueue(conn, "feu", str(f["id"]), stamp=stamp, trigger="rebuild")
    # Toute la vague d'indexation courante (concernées + historique ≥ seuil) : les pages
    # générées = celles listées au sitemap (Spec 04 §5, zéro 404).
    for c in communes_indexables(conn, config):
        n += enqueue(conn, "commune", c["code_insee"], stamp=stamp, trigger="rebuild")
    conn.commit()
    stats = consume(conn, config, stamp=stamp)
    site = finalize_site(conn, config)
    print(
        f"rebuild : {n} pages ré-enfilées → {stats['feu']} feux, {stats['commune']} communes, "
        f"{stats['carte']} carte, {stats['erreurs']} erreurs ; {site['pages']} pages, "
        f"{site['departements']} départements, sitemaps {site['sitemaps']} → {config['generate']['site_dir']}"
    )


def cmd_contexte() -> None:
    from vigifeu.engine.commune_context import refresh_commune_context

    conn, config = _open()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    res = refresh_commune_context(conn, config, valid_date=today)
    print(
        f"contexte : {res['communes']} communes concernées, {res['depts']} depts "
        f"(drought={res['drought_activated']}, vigieau={res['vigieau_activated']}) — "
        f"vigieau {res['vigieau_inserted']}, effis {res['effis_inserted']}, "
        f"meteo_forets {res['meteo_forets_inserted']}"
    )


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd, rest = args[0], args[1:]
    match cmd:
        case "init":
            cmd_init()
        case "fetch":
            cmd_fetch(rest[0] if rest else None)
        case "backfill":
            cmd_backfill(rest[0], rest[1])
        case "backfill-gaps":
            cmd_backfill_gaps()
        case "archive":
            cmd_archive()
        case "latence":
            cmd_latence()
        case "runs":
            cmd_runs(int(rest[0]) if rest else 20)
        case "moteur":
            cmd_moteur()
        case "rejeu":
            cmd_rejeu()
        case "feux":
            cmd_feux(int(rest[0]) if rest else 30)
        case "sources":
            cmd_sources()
        case "confirmer":
            cmd_confirmer(int(rest[0]), rest[1] if len(rest) > 1 else None)
        case "invalider":
            cmd_invalider(int(rest[0]))
        case "communes-import":
            cmd_communes_import(rest[0], _flag(rest, "millesime"), _flag(rest, "layer"))
        case "bdiff-import":
            cmd_bdiff_import(rest[0], "--replace" in rest)
        case "poi-import":
            cmd_poi_import(rest[0], _flag(rest, "source"))
        case "contexte":
            cmd_contexte()
        case "generer":
            cmd_generer(int(_flag(rest, "limit")) if _flag(rest, "limit") else None)
        case "rebuild":
            cmd_rebuild()
        case _:
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    main()
