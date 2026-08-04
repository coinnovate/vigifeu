-- Vigifeu — migration 006 : bulletins de veille presse (Spec 09, phase 2)
--
-- Une table pour l'enrichissement « veille presse » de la fiche feu :
--   bulletin — synthèse presse quotidienne d'un feu, produite par l'API
--              news.co-innovate.eu (champ `resume` = corps lisible), stockée
--              de façon IMMUABLE (Spec 01 P1) et affichée en timeline datée.
--
-- Principes portés (Spec 01 §1, Spec 09) :
--   P1  observation immuable : on insère, on ne réécrit jamais, on ne supprime jamais ;
--   P3  double horodatage : acq_at (borne de la veille) / ingested_at (quand on l'a su),
--       ingested_at JAMAIS réécrit (mesure de latence) ;
--   P4  catégorie `declaree` portée par la TABLE (convention, comme weather_obs=mesuree) :
--       tout bulletin est de la donnée presse tierce attribuée, jamais `mesuree`.
--
-- Idempotence : au plus UN bulletin par (fire_event_id, date_bulletin) — un rejeu du job
-- le même jour est un no-op (P1 : pas d'écrasement). Les tentatives et erreurs (429, timeout,
-- feu sans presse) ne sont PAS des lignes ici : elles vont dans ingestion_run (Spec 01 §3.7).
--
-- Contenu tiers : `resume`/indicateurs sont du texte d'un service externe → échappés au rendu
-- (autoescape Jinja), jamais affichés bruts. Règles juridiques dures : Spec 09 §10
-- (faits seulement, liens pas extraits, comptes pas de noms, pas de photo).

CREATE TABLE bulletin (
    id                INTEGER PRIMARY KEY,
    fire_event_id     INTEGER NOT NULL REFERENCES fire_event(id),
    date_bulletin     TEXT NOT NULL,      -- YYYY-MM-DD (date Europe/Paris, cf. Spec 09 §5)
    mots_cles         TEXT NOT NULL,      -- mot-clé effectivement envoyé à l'API (traçabilité, §3)
    resume            TEXT,               -- corps du bulletin (`resume` de l'API) — peut être vide
    indicateurs_json  TEXT,               -- liste {indicateur, valeur, statut, sources} (2e niveau)
    sources_json      TEXT,               -- URLs distinctes citées (dédupliquées des indicateurs)
    articles_valides  INTEGER,            -- nb d'articles retenus (contexte de fiabilité)
    fournisseurs_ia   TEXT,               -- `fournisseurs_ia` de l'API (traçabilité)
    provider          TEXT NOT NULL,      -- 'co-innovate' (source, pour un futur changement transparent)
    acq_at            TEXT NOT NULL,      -- borne de la veille = phénomène (P3-a)
    ingested_at       TEXT NOT NULL,      -- quand nous l'avons su (P3-b) ; JAMAIS réécrit
    UNIQUE (fire_event_id, date_bulletin) -- idempotence : un bulletin par feu et par jour
);
CREATE INDEX idx_bulletin_fire ON bulletin (fire_event_id, date_bulletin DESC);

INSERT INTO schema_version (version, applied_at)
VALUES (6, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
