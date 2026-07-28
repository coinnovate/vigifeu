"""Orchestration drought/vigieau par commune (Lot 3, L3.7).

On vérifie : (1) seules les communes en relation courante avec un feu actif confirmé
sont ciblées ; (2) flag off ⇒ aucun HTTP (marche à blanc) ; (3) flag on ⇒ un appel
par commune (vigieau, EFFIS) et un par département (Météo des forêts). Les fetchers
réels sont mockés (formats d'API non vérifiés — cadrage Lot 3).
"""

from __future__ import annotations

from vigifeu.engine import commune_context
from vigifeu.engine.commune_context import concerned_communes, refresh_commune_context


def _fire(conn, event_id, lifecycle="actif", qualif="vegetation_confirme"):
    conn.execute(
        "INSERT INTO fire_event (id, created_at, qualification, lifecycle) "
        "VALUES (?, '2026-07-22T00:00:00Z', ?, ?)",
        (event_id, qualif, lifecycle),
    )


def _commune(conn, code, dept):
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom, dept, centroid_lat, centroid_lon) "
        "VALUES (?, ?, ?, ?, 45.0, -1.0)",
        (code, f"c-{code}", code, dept),
    )


def _rel(conn, fire_id, code, *, valid_to=None):
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from, valid_to) "
        "VALUES (?, ?, 'emprise_dans_commune', '2026-07-22T12:00:00Z', ?)",
        (fire_id, code, valid_to),
    )


def _scenario(conn):
    _fire(conn, 1, "actif")               # feu actif confirmé
    _fire(conn, 2, "plus_detecte")        # feu non actif → à ignorer
    _commune(conn, "33001", "33")
    _commune(conn, "33002", "33")
    _commune(conn, "40001", "40")
    _commune(conn, "33099", "33")
    _rel(conn, 1, "33001")                # concernée (relation courante)
    _rel(conn, 1, "33002")                # concernée
    _rel(conn, 1, "40001")                # concernée (autre dept)
    _rel(conn, 1, "33099", valid_to="2026-07-23T00:00:00Z")  # relation fermée → hors
    _rel(conn, 2, "33001")                # feu non actif → n'ajoute rien


def test_concerned_communes(db):
    conn, _ = db
    _scenario(conn)
    codes = {c["code_insee"] for c in concerned_communes(conn)}
    assert codes == {"33001", "33002", "40001"}


def test_flag_off_marche_a_blanc(db, monkeypatch):
    conn, config = db
    _scenario(conn)
    calls = []
    monkeypatch.setattr(commune_context, "fetch_vigieau",
                        lambda *a, **k: calls.append("v") or {"inserted": 1})
    res = refresh_commune_context(conn, config, valid_date="2026-07-22")
    assert res["drought_activated"] is False and res["vigieau_activated"] is False
    assert res["communes"] == 3 and res["depts"] == 2
    assert res["vigieau_inserted"] == 0
    assert calls == []  # aucun fetch tiré


def test_flag_on_orchestre(db, monkeypatch):
    conn, config = db
    _scenario(conn)
    v, effis, mf = [], [], []
    monkeypatch.setattr(commune_context, "fetch_vigieau",
                        lambda conn, cfg, code: v.append(code) or {"inserted": 1})
    monkeypatch.setattr(commune_context, "fetch_effis_fwi",
                        lambda conn, cfg, **k: effis.append(k["code_insee"]) or {"inserted": 2})
    monkeypatch.setattr(commune_context, "fetch_meteo_forets",
                        lambda conn, cfg, **k: mf.append(k["dept"]) or {"inserted": 1})
    res = refresh_commune_context(
        conn, config, valid_date="2026-07-22",
        drought_activated=True, vigieau_activated=True,
    )
    # un vigieau + un EFFIS par commune concernée (3), un Météo des forêts par dept (2)
    assert sorted(v) == ["33001", "33002", "40001"]
    assert sorted(effis) == ["33001", "33002", "40001"]
    assert sorted(mf) == ["33", "40"]
    assert res["vigieau_inserted"] == 3
    assert res["effis_inserted"] == 6
    assert res["meteo_forets_inserted"] == 2
