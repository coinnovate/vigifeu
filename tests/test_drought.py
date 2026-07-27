"""Tests des fetchers sécheresse EFFIS et Météo des forêts (Spec 01 §3.5)."""

from __future__ import annotations

from vigifeu.ingest import drought

EFFIS_JSON = {"fwi": 38.5, "ffmc": 91.2, "dmc": 120.0, "dc": 640.0, "isi": 12.1, "bui": 180.0}


def test_effis_une_ligne_par_indice(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(drought, "_fetch_json", lambda *a, **k: EFFIS_JSON)
    r = drought.fetch_effis_fwi(conn, config, lat=44.9, lon=-1.15, valid_date="2026-07-22")
    assert r["status"] == "ok" and r["inserted"] == 6

    rows = {x["indicator"]: x for x in conn.execute("SELECT * FROM drought_obs")}
    assert set(rows) == {"fwi", "ffmc", "dmc", "dc", "isi", "bui"}
    assert rows["fwi"]["value"] == 38.5
    assert rows["fwi"]["lat"] == 44.9
    assert rows["fwi"]["provider"] == "effis"
    assert rows["fwi"]["value_class"] is None  # brut ; la traduction est du code (§3.5)


def test_effis_anti_doublon(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(drought, "_fetch_json", lambda *a, **k: EFFIS_JSON)
    drought.fetch_effis_fwi(conn, config, lat=44.9, lon=-1.15, valid_date="2026-07-22")
    r2 = drought.fetch_effis_fwi(conn, config, lat=44.9, lon=-1.15, valid_date="2026-07-22")
    assert r2["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM drought_obs").fetchone()["n"] == 6


def test_effis_indices_partiels(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(drought, "_fetch_json", lambda *a, **k: {"fwi": 40.0, "dc": 700.0})
    r = drought.fetch_effis_fwi(conn, config, lat=43.0, lon=6.0, valid_date="2026-07-23")
    assert r["inserted"] == 2


def test_meteo_forets_value_class(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(drought, "_fetch_json", lambda *a, **k: {"value_class": "rouge", "value": 4})
    r = drought.fetch_meteo_forets(conn, config, dept="33", valid_date="2026-07-22")
    assert r["status"] == "ok" and r["inserted"] == 1
    row = conn.execute("SELECT * FROM drought_obs WHERE indicator='meteo_forets'").fetchone()
    assert row["value_class"] == "rouge"
    assert row["dept"] == "33"
    assert row["code_insee"] is None and row["lat"] is None  # maille département


def test_meteo_forets_anti_doublon(db, monkeypatch):
    conn, config = db
    monkeypatch.setattr(drought, "_fetch_json", lambda *a, **k: {"value_class": "orange"})
    drought.fetch_meteo_forets(conn, config, dept="33", valid_date="2026-07-22")
    r2 = drought.fetch_meteo_forets(conn, config, dept="33", valid_date="2026-07-22")
    assert r2["inserted"] == 0


def test_panne_ne_bloque_pas(db, monkeypatch):
    conn, config = db

    def boom(*a, **k):
        raise drought.DroughtError("HTTP 503 (réessayable)")

    monkeypatch.setattr(drought, "_fetch_json", boom)
    r = drought.fetch_effis_fwi(conn, config, lat=44.9, lon=-1.15, valid_date="2026-07-22")
    assert r["status"] == "error" and "503" in r["error"]
    assert conn.execute("SELECT COUNT(*) AS n FROM drought_obs").fetchone()["n"] == 0
