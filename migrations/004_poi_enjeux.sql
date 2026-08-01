-- Vigifeu — migration 004 : référentiel POI / enjeux (Spec 06, phase 2, bloc 1)
--
-- Trois tables pour l'enrichissement « enjeux » des fiches feu et commune :
--   poi          — référentiel de points d'intérêt (OSM → BD TOPO → Géorisques),
--                  sœur de commune, mis à jour par millésimes (upsert idempotent) ;
--   fe_poi_rel   — relation feu ↔ POI (proximité à l'union des cellules), sœur de
--                  fe_commune_rel : historisée (valid_from/valid_to), jamais supprimée ;
--   commune_poi  — relation commune ↔ POI (point-dans-polygone), quasi-statique,
--                  recalculée à l'import d'un référentiel (recensement de la fiche commune).
--
-- Principes portés (Spec 01 §1, Spec 06) :
--   P5  provenance + date par POI (source, source_ref, imported_at) — fraîcheur = responsabilité ;
--   category et rel_type SANS CHECK : le jeu v1 (campings / écoles / hôpitaux-EHPAD /
--       stations-service / ICPE-Seveso) s'élargira (v1.1), on n'impose pas de migration —
--       même décision que fe_commune_rel.rel_type (migration 002).
--
-- Géométrie : un POI est un point → lat/lon WGS84 REAL (cohérent hotspot_raw / fixed_source),
-- pas de WKT (réservé aux contours communaux). Calculs métriques en Lambert-93 (engine.geo).

-- Spec 06 §2.1 — poi : référentiel (immuable dans l'esprit P1 ; upsert par clé naturelle)
CREATE TABLE poi (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,          -- osm / bdtopo / georisques
    source_ref   TEXT NOT NULL,          -- clé naturelle dans la source (ex. node/12345)
    category     TEXT NOT NULL,          -- camping/ecole/hopital/station_service/icpe_seveso (v1 ; sans CHECK)
    nom          TEXT,                   -- interne ; NON affiché au public en v1 (Spec 06 §4)
    lat          REAL NOT NULL,          -- WGS84 (cohérent hotspot_raw)
    lon          REAL NOT NULL,
    enjeu_json   TEXT,                   -- capacité, seuil Seveso… — usage abonné, pas public v1
    imported_at  TEXT NOT NULL,          -- P5 ; ISO UTC ; jamais réécrit sans changement de source
    UNIQUE (source, source_ref)          -- clé d'idempotence (upsert au ré-import)
);
CREATE INDEX idx_poi_category ON poi (category);

-- Spec 06 §3.1 — fe_poi_rel : feu ↔ POI (proximité), historisée comme fe_commune_rel
-- rel_type non contraint (paliers emprise / a_moins_de_X : décision du code, pas du schéma)
CREATE TABLE fe_poi_rel (
    id                    INTEGER PRIMARY KEY,
    fire_event_id         INTEGER NOT NULL REFERENCES fire_event(id),
    poi_id                INTEGER NOT NULL REFERENCES poi(id),
    rel_type              TEXT NOT NULL,   -- emprise / a_moins_de_{5,10,20}km
    distance_km           REAL,
    valid_from            TEXT NOT NULL,   -- ISO UTC
    valid_to              TEXT,            -- NULL = relation courante ; jamais supprimée, fermée
    computed_from_version INTEGER REFERENCES fire_event_version(id)
);
CREATE INDEX idx_fepr_fire ON fe_poi_rel (fire_event_id);
CREATE INDEX idx_fepr_poi ON fe_poi_rel (poi_id);
CREATE INDEX idx_fepr_courante ON fe_poi_rel (fire_event_id) WHERE valid_to IS NULL;

-- Spec 06 §3.2 — commune_poi : commune ↔ POI (point-dans-polygone), quasi-statique
-- Recalculée à l'import d'un référentiel (POI ou communes), pas à chaque cycle.
CREATE TABLE commune_poi (
    code_insee  TEXT NOT NULL REFERENCES commune(code_insee),
    poi_id      INTEGER NOT NULL REFERENCES poi(id),
    PRIMARY KEY (code_insee, poi_id)
);
CREATE INDEX idx_commune_poi_poi ON commune_poi (poi_id);

INSERT INTO schema_version (version, applied_at)
VALUES (4, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
