-- Vigifeu — migration 001 : socle Lot 0
-- Périmètre volontairement minimal : ce qu'il faut pour que « le magnétophone tourne ».
-- Le schéma complet (FireEvent, communes, etc.) arrive au Lot 1 (Spec 01 intégrale).

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL           -- ISO UTC
);

-- Spec 01 §5.1 — référentiel des sources satellitaires (peuplé depuis config/params.toml)
CREATE TABLE satellite_source (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE, -- ex. VIIRS_SNPP_NRT (identifiant d'API FIRMS)
    platform      TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    resolution_m  INTEGER,
    active        INTEGER NOT NULL DEFAULT 1,
    notes         TEXT
);

-- Spec 01 §3.7 — journal d'ingestion : la boîte noire du système
CREATE TABLE ingestion_run (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,         -- ex. firms:VIIRS_SNPP_NRT
    params       TEXT,                  -- JSON (jour demandé, bbox…)
    started_at   TEXT NOT NULL,         -- ISO UTC
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',  -- running / ok / error
    http_status  INTEGER,
    n_rows       INTEGER,               -- lignes reçues de la source
    n_new        INTEGER,               -- lignes réellement nouvelles (idempotence)
    error_text   TEXT
);

-- Spec 01 §3.1 — la table la plus importante du système
CREATE TABLE hotspot_raw (
    id                INTEGER PRIMARY KEY,
    source_id         INTEGER NOT NULL REFERENCES satellite_source(id),
    lat               REAL NOT NULL,
    lon               REAL NOT NULL,
    acq_at            TEXT NOT NULL,    -- ISO UTC — horodatage du phénomène (P3-a)
    ingested_at       TEXT NOT NULL,    -- ISO UTC — première apparition chez nous (P3-b)
    ingestion_run_id  INTEGER NOT NULL REFERENCES ingestion_run(id),
    frp_mw            REAL,
    confidence        TEXT,             -- valeur source brute (l/n/h), non normalisée
    scan_km           REAL,
    track_km          REAL,
    day_night         TEXT,             -- D / N
    raw_payload       TEXT,             -- ligne CSV source complète (audit)
    overpass_id       INTEGER,          -- Lot 1
    fixed_source_id   INTEGER,          -- Lot 2
    -- Clé de déduplication à l'ingestion (Spec 01 §3.1) : réingérer un jour connu = no-op.
    UNIQUE (source_id, lat, lon, acq_at)
);

CREATE INDEX idx_hotspot_acq ON hotspot_raw (acq_at);
CREATE INDEX idx_hotspot_ingested ON hotspot_raw (ingested_at);

-- La mesure de latence NRT (cadrage §6bis) est une simple vue : ingested_at − acq_at.
-- Aucun code supplémentaire : le protocole de monitoring est le schéma lui-même.
CREATE VIEW v_latence_nrt AS
SELECT
    s.code                                            AS source,
    h.acq_at,
    h.ingested_at,
    ROUND((julianday(h.ingested_at) - julianday(h.acq_at)) * 24, 2) AS latence_h
FROM hotspot_raw h
JOIN satellite_source s ON s.id = h.source_id;

INSERT INTO schema_version (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
