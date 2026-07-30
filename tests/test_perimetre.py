"""Périmètre d'indexation par vagues (Spec 04 §5) : communes concernées OU historique ≥ seuil."""

from __future__ import annotations

from vigifeu.generate.perimetre import communes_indexables, depts_indexables


def _commune(conn, code, dept, slug, nom):
    conn.execute(
        "INSERT INTO commune (code_insee, dept, slug, nom) VALUES (?,?,?,?)",
        (code, dept, slug, nom),
    )


def _histo(conn, code, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO commune_fire_history (code_insee, annee, source_base) VALUES (?,?, 'bdiff')",
            (code, 2010 + i),
        )


def test_communes_indexables_seuil(db):
    conn, config = db
    config["generate"]["wave_min_history_fires"] = 3
    _commune(conn, "33001", "33", "a-concernee", "A")     # concernée (fe_commune_rel)
    _commune(conn, "33002", "33", "b-riche", "B")         # 4 feux historiques → dans la vague
    _commune(conn, "33003", "33", "c-pauvre", "C")        # 1 feu → hors vague
    _commune(conn, "40004", "40", "d-riche", "D")         # 3 feux (seuil) → dans la vague
    # une relation feu↔commune pour 33001 (concernée) — feu minimal
    fe = conn.execute("INSERT INTO fire_event (created_at, lifecycle) VALUES ('t','actif')").lastrowid
    conn.execute("INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from) "
                 "VALUES (?, '33001', 'emprise_dans_commune', 't')", (fe,))
    _histo(conn, "33002", 4)
    _histo(conn, "33003", 1)
    _histo(conn, "40004", 3)
    conn.commit()

    codes = {r["code_insee"] for r in communes_indexables(conn, config)}
    assert codes == {"33001", "33002", "40004"}   # concernée + les ≥3, pas la commune à 1 feu
    assert depts_indexables(conn, config) == ["33", "40"]

    # abaisser le seuil élargit la vague (33003 entre)
    config["generate"]["wave_min_history_fires"] = 1
    codes2 = {r["code_insee"] for r in communes_indexables(conn, config)}
    assert "33003" in codes2
