-- Vigifeu — migration 002 : schéma intégral Spec 01 (Lot 1)
--
-- Complète le socle minimal du Lot 0 (migration 001) avec toutes les tables
-- restantes du modèle de données : observations, interprétation, référentiels.
--
-- Principes portés par le schéma (Spec 01 §1) :
--   P1  observations immuables — aucune suppression (au pire un marquage) ;
--   P3  double horodatage (phénomène + ingestion) sur toute observation ;
--   P4  catégorie de donnée explicite — vocabulaires contrôlés par CHECK ;
--   P7  tout est UTC (TEXT ISO), conversion locale à l'affichage.
--
-- Note FK : hotspot_raw (migration 001) porte déjà les colonnes overpass_id et
-- fixed_source_id sans contrainte FK déclarée. La table est immuable et déjà
-- peuplée en production (P1) : on ne la recrée pas pour formaliser ces FK.
-- Les tables cibles existent après cette migration ; la relation est honorée
-- par le code du pipeline (Lot 2).

-- ============================================================================
-- 5. Référentiels
-- ============================================================================

-- Spec 01 §5.2 — commune (Admin Express, importée au Lot 3)
CREATE TABLE commune (
    code_insee                  TEXT PRIMARY KEY,     -- identifiant stable et public (P6)
    slug                        TEXT NOT NULL,        -- ex. le-porge → /communes/33333-le-porge
    nom                         TEXT NOT NULL,
    dept                        TEXT,
    region                      TEXT,
    epci_code                   TEXT,
    population                  INTEGER,              -- millésime en métadonnée
    geometry_wkt                TEXT,                 -- contour Admin Express (WKT ; PostGIS plus tard)
    centroid_lat                REAL,
    centroid_lon                REAL,
    surface_ha                  REAL,
    surface_forestiere_ha       REAL,                 -- précalcul BD Forêt / CLC
    pprif                       TEXT,                 -- statut (prescrit/approuvé/néant) + réf
    obligation_debroussaillement INTEGER,            -- booléen NULL
    exposition_structurelle     REAL,                 -- score précalculé (module ultérieur)
    referentiel_millesime       TEXT                  -- version Admin Express source
);

-- Spec 01 §5.2 — succession des communes nouvelles : les codes INSEE morts redirigent
CREATE TABLE commune_succession (
    id            INTEGER PRIMARY KEY,
    ancien_code   TEXT NOT NULL,
    nouveau_code  TEXT NOT NULL,
    date_effet    TEXT NOT NULL                        -- ISO (jour)
);
CREATE INDEX idx_succession_ancien ON commune_succession (ancien_code);

-- Spec 01 §5.3 — historique des feux (BDIFF 2006+ / Prométhée 1973+ arc méditerranéen)
CREATE TABLE commune_fire_history (
    id           INTEGER PRIMARY KEY,
    code_insee   TEXT NOT NULL REFERENCES commune(code_insee),
    annee        INTEGER NOT NULL,
    date_alerte  TEXT,                                 -- ISO
    surface_ha   REAL,
    type_feu     TEXT,
    source_base  TEXT NOT NULL,                        -- bdiff / promethee
    source_ref   TEXT,
    CHECK (source_base IN ('bdiff', 'promethee'))
);
CREATE INDEX idx_cfh_commune ON commune_fire_history (code_insee);
CREATE INDEX idx_cfh_annee ON commune_fire_history (annee);

-- ============================================================================
-- 4. Interprétation : le feu (recalculable, versionnée)
-- ============================================================================

-- Spec 01 §4.1 — fire_event : identité stable, publique (P6)
CREATE TABLE fire_event (
    id                   INTEGER PRIMARY KEY,
    public_id            TEXT UNIQUE,                  -- ex. 2026-saumos ; NULL tant que non publié (suspects)
    created_at           TEXT NOT NULL,               -- ISO UTC
    first_acq_at         TEXT,                         -- première détection du cluster (donnée contractuelle)
    last_acq_at          TEXT,
    qualification        TEXT,                         -- rempli par le moteur (Lot 2)
    qualification_reason TEXT,                         -- trace de la règle appliquée (explicabilité)
    lifecycle            TEXT NOT NULL DEFAULT 'actif',
    merged_into          INTEGER REFERENCES fire_event(id),  -- FireEvent absorbant si fusionne
    confidence_level     TEXT,
    CHECK (qualification IS NULL OR qualification IN
        ('vegetation_confirme', 'suspect_source_fixe', 'suspect_isole', 'faux_positif')),
    CHECK (lifecycle IN ('actif', 'plus_detecte', 'fusionne', 'archive')),
    CHECK (confidence_level IS NULL OR confidence_level IN
        ('confirme', 'probable', 'signalement'))
);
CREATE INDEX idx_fe_lifecycle ON fire_event (lifecycle);

-- Spec 01 §4.2 — fire_event_version : états successifs (support de la relecture de propagation)
CREATE TABLE fire_event_version (
    id                       INTEGER PRIMARY KEY,
    fire_event_id            INTEGER NOT NULL REFERENCES fire_event(id),
    version_n                INTEGER NOT NULL,
    computed_at              TEXT NOT NULL,            -- ISO UTC
    trigger_ingestion_run_id INTEGER REFERENCES ingestion_run(id),
    geometry_wkt             TEXT,                     -- enveloppe (hull)
    n_hotspots               INTEGER,
    n_hotspots_dedup         INTEGER,                  -- dédupliqué inter-satellites
    frp_total_last_pass_mw   REAL,                     -- par passage, pas cumulé (comparabilité §7ter)
    area_ha_estimee          REAL,                     -- catégorie estimee (suffixe _estimee, §7)
    front_progress_km        REAL,                     -- progression mesurée entre versions comparables
    front_bearing_deg        REAL,
    stats_json               TEXT,                     -- reste des agrégats
    UNIQUE (fire_event_id, version_n)
);
CREATE INDEX idx_fev_fire ON fire_event_version (fire_event_id);

-- Spec 01 §4.2 — fe_hotspot : lien version ↔ hotspot, avec groupe de dédup par passage
CREATE TABLE fe_hotspot (
    id                    INTEGER PRIMARY KEY,
    fire_event_version_id INTEGER NOT NULL REFERENCES fire_event_version(id),
    hotspot_id            INTEGER NOT NULL REFERENCES hotspot_raw(id),
    dedup_group           TEXT                          -- pixels SNPP/NOAA-20 d'un même point physique
);
CREATE INDEX idx_feh_version ON fe_hotspot (fire_event_version_id);
CREATE INDEX idx_feh_hotspot ON fe_hotspot (hotspot_id);

-- Spec 01 §4.3 — fire_cell_state : cycle de vie spatial (grille ~750 m, état courant en v1)
CREATE TABLE fire_cell_state (
    id            INTEGER PRIMARY KEY,
    fire_event_id INTEGER NOT NULL REFERENCES fire_event(id),
    cell_key      TEXT NOT NULL,                        -- indice de grille
    lat           REAL,
    lon           REAL,
    first_acq_at  TEXT,
    last_acq_at   TEXT,
    frp_max_mw    REAL,
    state         TEXT,                                 -- front_actif / recent / plus_detecte (calculé)
    CHECK (state IS NULL OR state IN ('front_actif', 'recent', 'plus_detecte'))
);
CREATE INDEX idx_fcs_fire ON fire_cell_state (fire_event_id);
CREATE UNIQUE INDEX idx_fcs_fire_cell ON fire_cell_state (fire_event_id, cell_key);

-- Spec 01 §4.4 — fixed_source : registre des sources fixes (torchères, industrie…)
CREATE TABLE fixed_source (
    id             INTEGER PRIMARY KEY,
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    radius_m       REAL,
    kind           TEXT,                                -- torchère / aciérie / raffinerie / inconnu
    evidence_json  TEXT,                                -- jours de présence, FRP moyen, emprise
    status         TEXT NOT NULL DEFAULT 'candidat',
    first_seen     TEXT,
    last_review_at TEXT,
    clc_code       TEXT,                                -- croisement Corine Land Cover
    CHECK (status IN ('confirme', 'candidat', 'invalide'))
);

-- Spec 01 §4.5 — fe_fe_rel : relations entre feux (fusion, proximité)
CREATE TABLE fe_fe_rel (
    id                     INTEGER PRIMARY KEY,
    fire_event_id          INTEGER NOT NULL REFERENCES fire_event(id),
    related_fire_event_id  INTEGER NOT NULL REFERENCES fire_event(id),
    rel_type               TEXT NOT NULL,               -- fusionne_dans / proche_de
    created_at             TEXT NOT NULL,
    note                   TEXT,
    CHECK (rel_type IN ('fusionne_dans', 'proche_de'))
);
CREATE INDEX idx_fefe_fire ON fe_fe_rel (fire_event_id);

-- Spec 01 §5.4 — fe_commune_rel : la relation cœur (feu ↔ commune, historisée)
-- rel_type non contraint par CHECK : l'encodage des paliers (a_moins_de_5km…) est
-- une décision du Lot 3 ; on ne veut pas forcer une migration à ce moment-là.
CREATE TABLE fe_commune_rel (
    id                    INTEGER PRIMARY KEY,
    fire_event_id         INTEGER NOT NULL REFERENCES fire_event(id),
    code_insee            TEXT NOT NULL REFERENCES commune(code_insee),
    rel_type              TEXT NOT NULL,                -- emprise_dans_commune / a_moins_de_X / direction_vent
    distance_km           REAL,                         -- distance à la limite communale (a_moins_de_X)
    valid_from            TEXT NOT NULL,                -- ISO UTC
    valid_to              TEXT,                         -- NULL = relation courante ; jamais supprimée, fermée
    computed_from_version INTEGER REFERENCES fire_event_version(id)
);
CREATE INDEX idx_fecr_fire ON fe_commune_rel (fire_event_id);
CREATE INDEX idx_fecr_commune ON fe_commune_rel (code_insee);
CREATE INDEX idx_fecr_courante ON fe_commune_rel (code_insee) WHERE valid_to IS NULL;

-- ============================================================================
-- 3. Observations brutes (immuables, double horodatage)
-- ============================================================================

-- Spec 01 §3.2 — overpass : passages satellite (regroupement dérivé, recalculable)
CREATE TABLE overpass (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES satellite_source(id),
    window_start  TEXT NOT NULL,                        -- ISO UTC
    window_end    TEXT NOT NULL,
    day_night     TEXT,                                 -- D / N
    n_hotspots    INTEGER
);
CREATE INDEX idx_overpass_source ON overpass (source_id, window_start);

-- Spec 01 §3.3 — weather_obs : météo constatée (mesuree / estimee)
CREATE TABLE weather_obs (
    id              INTEGER PRIMARY KEY,
    fire_event_id   INTEGER NOT NULL REFERENCES fire_event(id),
    lat             REAL,
    lon             REAL,
    observed_at     TEXT NOT NULL,                       -- validité de la mesure (P3-a)
    fetched_at      TEXT NOT NULL,                       -- quand récupérée (P3-b)
    provider        TEXT,                                -- open-meteo / meteo-france / …
    wind_speed_kmh  REAL,
    wind_gusts_kmh  REAL,
    wind_dir_deg    REAL,
    temp_c          REAL,
    rh_pct          REAL,
    precip_mm_1h    REAL,
    precip_mm_24h   REAL
);
CREATE INDEX idx_wobs_fire ON weather_obs (fire_event_id, observed_at);

-- Spec 01 §3.4 — weather_forecast : prévisions officielles (prevue), une par run
CREATE TABLE weather_forecast (
    id               INTEGER PRIMARY KEY,
    fire_event_id    INTEGER REFERENCES fire_event(id),  -- cible feu…
    code_insee       TEXT REFERENCES commune(code_insee),-- …ou commune
    provider         TEXT,
    model            TEXT,                                -- ex. AROME
    model_run_at     TEXT NOT NULL,                       -- run du modèle (identifie la prévision)
    valid_at         TEXT NOT NULL,                       -- échéance
    precip_mm        REAL,
    precip_prob_pct  REAL,
    wind_speed_kmh   REAL,
    wind_dir_deg     REAL,
    temp_c           REAL,
    rh_pct           REAL,
    fetched_at       TEXT NOT NULL,
    CHECK (fire_event_id IS NOT NULL OR code_insee IS NOT NULL)
);
CREATE INDEX idx_wfc_fire ON weather_forecast (fire_event_id, valid_at);
CREATE INDEX idx_wfc_commune ON weather_forecast (code_insee, valid_at);

-- Spec 01 §3.5 — drought_obs : sécheresse et danger, multi-indices, maille variable
CREATE TABLE drought_obs (
    id           INTEGER PRIMARY KEY,
    indicator    TEXT NOT NULL,                          -- fwi/ffmc/dmc/dc/isi/bui/meteo_forets/sim_swi
    code_insee   TEXT REFERENCES commune(code_insee),    -- maille commune…
    dept         TEXT,                                   -- …département (Météo des forêts)…
    lat          REAL,                                   -- …ou point de grille
    lon          REAL,
    valid_date   TEXT NOT NULL,                          -- jour (ou décade SIM)
    value        REAL,                                   -- valeur brute
    value_class  TEXT,                                   -- classe officielle si la source en fournit
    provider     TEXT,
    fetched_at   TEXT,
    CHECK (indicator IN
        ('fwi', 'ffmc', 'dmc', 'dc', 'isi', 'bui', 'meteo_forets', 'sim_swi')),
    CHECK (code_insee IS NOT NULL OR dept IS NOT NULL OR (lat IS NOT NULL AND lon IS NOT NULL))
);
CREATE INDEX idx_drought_commune ON drought_obs (code_insee, indicator, valid_date);
CREATE INDEX idx_drought_dept ON drought_obs (dept, indicator, valid_date);

-- Spec 01 §3.6 — vigieau_arrete : restrictions d'eau (declaree, source officielle)
CREATE TABLE vigieau_arrete (
    id           INTEGER PRIMARY KEY,
    code_insee   TEXT NOT NULL REFERENCES commune(code_insee),
    niveau       TEXT NOT NULL,                          -- vigilance/alerte/alerte_renforcee/crise
    date_debut   TEXT NOT NULL,
    date_fin     TEXT,                                   -- NULL = en cours
    arrete_ref   TEXT,
    fetched_at   TEXT,
    CHECK (niveau IN ('vigilance', 'alerte', 'alerte_renforcee', 'crise'))
);
CREATE INDEX idx_vigieau_commune ON vigieau_arrete (code_insee);

-- ============================================================================
-- 3.8 Tables techniques
-- ============================================================================

-- Spec 01 §3.8 — geo_detection_raw : RÉSERVÉE phase 2 (MTG-FCI FIR), structure sœur de hotspot_raw
CREATE TABLE geo_detection_raw (
    id                        INTEGER PRIMARY KEY,
    source_id                 INTEGER REFERENCES satellite_source(id),
    lat                       REAL NOT NULL,
    lon                       REAL NOT NULL,
    acq_at                    TEXT NOT NULL,             -- P3-a
    ingested_at               TEXT NOT NULL,             -- P3-b
    intensity                 REAL,
    confidence                TEXT NOT NULL DEFAULT 'probable',
    raw_payload               TEXT,
    confirmed_by_fire_event_id INTEGER REFERENCES fire_event(id)  -- renseigné à la confirmation VIIRS
);

-- Spec 01 §3.8 — regen_queue : file de régénération consommée par le générateur (Spec 04)
CREATE TABLE regen_queue (
    id           INTEGER PRIMARY KEY,
    page_type    TEXT NOT NULL,                          -- carte / feu / commune / sitemap
    page_ref     TEXT,                                   -- identifiant de la page (public_id, code_insee…)
    enqueued_at  TEXT NOT NULL,
    processed_at TEXT,                                   -- NULL = en attente
    trigger      TEXT,                                   -- run d'ingestion ou tâche à l'origine
    CHECK (page_type IN ('carte', 'feu', 'commune', 'sitemap'))
);
CREATE INDEX idx_regen_attente ON regen_queue (page_type) WHERE processed_at IS NULL;

-- ============================================================================
INSERT INTO schema_version (version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
