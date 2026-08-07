"""Validation MTG contre le feu de Saumos (Spec 07 §10, calibration de fire_classes + anti-glint).

Tire les granules 0682 d'une fenêtre donnée et sort les pixels feu **près de Saumos** (~44.90 N,
-1.02 E, le méga-feu de référence des 22-25/07) avec leur **classe** et leur **probabilité**. But :
voir si MTG a capté un vrai grand feu et, si oui, à partir de quelle classe/probabilité le signal
RÉEL se sépare du glint côtier (classe 1, basse proba) observé le 06/08.

Usage (identifiants EUMETSAT en environnement) :
    python scripts/mtg_validate_saumos.py [YYYY-MM-DD] [h0_utc] [h1_utc]
    # défaut : 2026-07-22 12 15  (après-midi de l'embrasement, UTC)
On peut viser un autre créneau/jour : ... mtg_validate_saumos.py 2026-07-23 11 14

Aucune écriture : lecture Data Store + parsing en mémoire uniquement.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from vigifeu.ingest.eumetsat import EumetsatClient
from vigifeu.ingest.mtg_netcdf import parse_fir
from vigifeu.model.db import load_config

SAUMOS = (44.90, -1.02)
BOX = (-1.6, 44.4, -0.4, 45.4)   # O,S,E,N — boîte généreuse autour de Saumos (bassin d'Arcachon)


def _near(pix):
    return [p for p in pix if BOX[0] <= p["lon"] <= BOX[2] and BOX[1] <= p["lat"] <= BOX[3]]


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-07-22"
    h0 = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    h1 = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    y, m, d = (int(x) for x in day.split("-"))
    cfg = load_config("config/params.toml")
    cli = EumetsatClient(cfg)
    since = datetime(y, m, d, h0, tzinfo=timezone.utc)
    until = datetime(y, m, d, h1, tzinfo=timezone.utc)

    prods = cli.list_products(since, until)
    print(f"Saumos ~{SAUMOS[0]},{SAUMOS[1]} — {day} {h0:02d}:00–{h1:02d}:00 UTC — {len(prods)} granule(s)")
    if not prods:
        print("Aucun granule (l'archive Data Store ne remonte peut-être pas si loin, ou fenêtre vide).")
        return

    classes: dict[str, int] = {}
    prob_max = 0.0
    total_pres = 0
    for p in prods:
        pix = parse_fir(cli.download(p["download_url"]), cfg,
                        bbox=cfg["mtg"]["bbox"], default_acq_at=p["sensing_at"])
        pres = _near(pix)
        total_pres += len(pres)
        print(f"\n  slot {p['sensing_at']} : {len(pix)} pixel(s) France, {len(pres)} près de Saumos")
        for x in sorted(pres, key=lambda z: -(z["probability"] or 0)):
            pr = f"{x['probability']:.2f}" if x["probability"] is not None else "?"
            print(f"      {x['lat']:.3f}, {x['lon']:.3f}  classe={x['confidence']}  prob={pr}")
            classes[x["confidence"]] = classes.get(x["confidence"], 0) + 1
            if x["probability"] is not None:
                prob_max = max(prob_max, x["probability"])

    print("\n=== Bilan près de Saumos ===")
    print(f"  {total_pres} pixel(s) au total ; classes {dict(sorted(classes.items()))} ; prob max {prob_max:.2f}")
    print("  → si un vrai grand feu ressort en classe 2/3 et/ou proba haute, alors que le glint du 06/08")
    print("    était classe 1 basse proba : on tient le seuil (fire_classes et/ou seuil fire_probability).")


if __name__ == "__main__":
    main()
