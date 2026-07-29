"""Régression carte nationale : lister les feux publiés dès le PREMIER build.

Bug prod (Lot 5, 1er `generer` réel) : `ensure_public_id` n'était appelé qu'au rendu de
chaque fiche feu (`_handle_feu`). Dans la file accumulée, la `carte` (enfilée une seule
fois) avait un `id` plus bas que TOUS les feux → elle était écrite AVANT l'assignation
des `public_id` → `index.html` sans aucun feu (« aucun feu suivi ») alors que des feux
actifs confirmés existaient. Le fixture des garde-fous masquait le bug en pré-assignant
les `public_id`.

Ici on reproduit l'ORDRE de prod : file remise à plat avec la carte AVANT les fiches
feux, aucune pré-assignation. On asserte sur le HTML/GeoJSON **écrit** (le symptôme est
dans le fichier produit, pas dans la base — qui finit cohérente de toute façon).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.regen import CARTE_REF, enqueue
from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.generate.runner import consume, sync_static
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources
from vigifeu.referentiels.communes import import_communes

from .conftest import load_saumos_hotspots

BBOX = (44.5, 45.3, -1.30, -0.30)
COMMUNES = "tests/fixtures/communes/gironde-ouest.geojson"
DAYS = [f"2026-07-{d:02d}" for d in range(20, 28)]


@pytest.fixture(scope="module")
def build_carte_avant_feux(tmp_path_factory):
    """Rejeu Saumos, puis file remise à plat avec la carte AVANT les feux (ordre prod).

    Aucune pré-assignation de public_id : consume doit publier lui-même avant de rendre
    la carte, sinon `index.html` sort sans feu.
    """
    out = tmp_path_factory.mktemp("site-carte")
    conn = connect(":memory:")
    migrate(conn)
    config = load_config("config/params.toml")
    config["generate"]["site_dir"] = str(out)
    sync_satellite_sources(conn, config)
    import_communes(conn, COMMUNES, millesime="test-gironde")
    invalidate_commune_index(conn)
    for d in DAYS:
        load_saumos_hotspots(conn, day_prefix=d, bbox=BBOX)
        build_overpasses(conn, config)
        process_cycle(conn, config, stamp=d + "T23:59:00Z")

    confirmes = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM fire_event WHERE qualification='vegetation_confirme'"
        )
    ]
    assert confirmes, "le rejeu n'a produit aucun feu confirmé"

    # File adversariale : carte d'abord (id le plus bas), fiches feux ensuite.
    conn.execute("DELETE FROM regen_queue")
    enqueue(conn, "carte", CARTE_REF, stamp="s", trigger="test")
    for fid in confirmes:
        enqueue(conn, "feu", str(fid), stamp="s", trigger="test")
    conn.commit()

    sync_static(config)
    consume(conn, config, stamp="2026-07-28T00:00:00Z")
    yield conn, config, out
    conn.close()


def test_index_html_reference_des_fiches_feux(build_carte_avant_feux):
    """index.html écrit doit référencer au moins une fiche feu (symptôme visible du bug)."""
    _, _, out = build_carte_avant_feux
    html = (Path(out) / "index.html").read_text(encoding="utf-8")
    assert "/feux/" in html, "carte générée sans aucune fiche feu (rendue avant les public_id)"


def test_feux_geojson_national_non_vide(build_carte_avant_feux):
    """Le GeoJSON national écrit porte au moins un marqueur de feu."""
    _, _, out = build_carte_avant_feux
    fc = json.loads((Path(out) / "feux.geojson").read_text(encoding="utf-8"))
    assert fc["features"], "GeoJSON national écrit vide"


def test_consume_publie_tous_les_feux_confirmes(build_carte_avant_feux):
    """Après consume, aucun feu confirmé ne reste sans public_id (cœur du correctif)."""
    conn, _, _ = build_carte_avant_feux
    reste = conn.execute(
        "SELECT COUNT(*) FROM fire_event "
        "WHERE qualification='vegetation_confirme' AND public_id IS NULL"
    ).fetchone()[0]
    assert reste == 0, "des feux confirmés restent sans public_id après consume"
