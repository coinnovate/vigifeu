-- Vigifeu — migration 007 : détection géostationnaire MTG (Spec 07, phase 2)
--
-- Deux tables pour le flux MTG FCI L2 « Active Fire Monitoring » (Data Store EO:EUM:DAT:0682),
-- flux SÉPARÉ et ÉTANCHE du socle VIIRS (jamais mélangé à hotspot_raw) :
--
--   geo_candidate     — objet interne LÉGER d'amorçage (early-detection). PAS un fire_event :
--                       préserve l'invariant « fire_event = adossé à des hotspots VIIRS » (Spec 07 §4).
--                       Un fire_event n'apparaît qu'À la confirmation VIIRS ; le candidat s'y rattache.
--                       Jamais de public_id, jamais de fiche : sa seule trace publique est le carré
--                       « signal en attente » rendu depuis geo_detection_raw.
--   geo_detection_raw — observation immuable (sœur de hotspot_raw), un pixel feu MTG par (slot, position).
--
-- Principes portés (Spec 01 §1, Spec 07 §0/§4/§6) :
--   P1  observation immuable : on insère, on ne réécrit jamais, on ne supprime jamais (hors archive/purge) ;
--   P3  double horodatage : acq_at (heure du slot 10 min) / ingested_at (quand on l'a su),
--       ingested_at JAMAIS réécrit (mesure de latence, vue v_latence_nrt_mtg) ;
--   P4  catégorie native `probable` portée par le flux (convention, comme weather_obs=mesuree) ;
--   §6  étanchéité puissance : geo_detection_raw.frp_mw sert la tendance MTG et la calibration, JAMAIS
--       versée dans frp_max/fire_event_version (non commensurable avec VIIRS).
--
-- Les colonnes de liaison (geo_candidate_id, confirmed_by_fire_event_id) sont des ANNOTATIONS mutables
-- (comme overpass_id/fixed_source_id sur hotspot_raw) : elles ne touchent ni acq_at ni ingested_at.
--
-- Idempotence d'ingestion : UNIQUE (provider, acq_at, lat, lon) — rejouer un granule connu = no-op.
--
-- Remplacement du placeholder : la migration 002 avait RÉSERVÉ une table geo_detection_raw d'ébauche
-- (source_id/intensity, avant le cadrage Spec 07). Elle n'a JAMAIS été alimentée (aucun fetcher, [mtg]
-- absent jusqu'ici) → on la DROP et on la recrée à la forme cadrée. Aucune observation détruite (P1 : la
-- table est vide), seule la structure évolue. La colonne confirmed_by_fire_event_id est conservée (nom
-- identique) : engine/pipeline.py (wipe/regen) continue de la remettre à NULL sans changement.

DROP TABLE geo_detection_raw;

-- geo_candidate d'abord (geo_detection_raw la référence). Elle-même référence fire_event (déjà présent).
CREATE TABLE geo_candidate (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,                       -- ISO UTC
    first_acq_at   TEXT NOT NULL,                       -- 1re détection MTG du candidat
    last_acq_at    TEXT NOT NULL,                       -- dernière détection rattachée (déclenche la régén, §7)
    centroid_lat   REAL NOT NULL,
    centroid_lon   REAL NOT NULL,
    n_detections   INTEGER NOT NULL,                    -- slots MTG distincts rattachés
    status         TEXT NOT NULL DEFAULT 'en_attente',  -- en_attente | confirme | expire
    fire_event_id  INTEGER REFERENCES fire_event(id),   -- posé à la confirmation VIIRS (promotion), NULL sinon
    CHECK (status IN ('en_attente', 'confirme', 'expire'))
);
CREATE INDEX idx_geo_candidate_status ON geo_candidate (status);

CREATE TABLE geo_detection_raw (
    id                         INTEGER PRIMARY KEY,
    provider                   TEXT NOT NULL,     -- 'mtg-fci-fir' (source lisible, pas de FK satellite_source)
    lat                        REAL NOT NULL,
    lon                        REAL NOT NULL,
    acq_at                     TEXT NOT NULL,     -- ISO UTC — heure du slot 10 min (phénomène, P3-a)
    ingested_at                TEXT NOT NULL,     -- ISO UTC — 1re apparition chez nous (P3-b) ; JAMAIS réécrit
    ingestion_run_id           INTEGER NOT NULL REFERENCES ingestion_run(id),
    frp_mw                     REAL,              -- FRP du 0682 (public-légal) — JAMAIS versée dans frp_max VIIRS
    frp_uncertainty_mw         REAL,
    confidence                 TEXT,              -- valeur source brute (non normalisée)
    quality_flag               TEXT,              -- issu de QualityProduct si exploité
    geo_candidate_id           INTEGER REFERENCES geo_candidate(id),  -- regroupement en candidat (§4.2), NULL sinon
    confirmed_by_fire_event_id INTEGER REFERENCES fire_event(id),     -- posé à la confirmation VIIRS (§5), NULL sinon
    raw_payload                TEXT,              -- attributs source du pixel (audit)
    UNIQUE (provider, acq_at, lat, lon)           -- idempotence : réingérer un slot connu = no-op
);
CREATE INDEX idx_geodet_acq ON geo_detection_raw (acq_at);
CREATE INDEX idx_geodet_ingested ON geo_detection_raw (ingested_at);
CREATE INDEX idx_geodet_candidate ON geo_detection_raw (geo_candidate_id);
CREATE INDEX idx_geodet_confirmed ON geo_detection_raw (confirmed_by_fire_event_id);

-- Latence NRT MTG : simple vue ingested_at − acq_at (le monitoring EST le schéma, cf. v_latence_nrt).
CREATE VIEW v_latence_nrt_mtg AS
SELECT
    g.provider                                        AS source,
    g.acq_at,
    g.ingested_at,
    ROUND((julianday(g.ingested_at) - julianday(g.acq_at)) * 24, 2) AS latence_h
FROM geo_detection_raw g;

INSERT INTO schema_version (version, applied_at)
VALUES (7, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
