-- Vigifeu — base CONTRIBUTIONS, migration 001 (Spec 10 : contributions photo public)
--
-- Base SÉPARÉE de la socle (Spec 10 §2/§3) : la mini-API en est l'écrivain. Ce cloisonnement
-- préserve l'invariant fondateur « un seul écrivain sur la base socle = le daemon » (plan §1.1)
-- et matérialise la lignée `declaree` distincte du satellite (P0). Schéma versionné à part
-- (sa propre table schema_version), indépendant des migrations socle 001–007.
--
-- Dérogation ENCADRÉE à P1 (immuabilité) : une contribution porte des données personnelles
-- (image de personnes possibles, IP, email éventuel) → le RGPD impose un cycle de vie + une
-- purge (Spec 10 §9). Seule cette base a un cycle de vie ; la socle reste immuable.

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL           -- ISO UTC
);

-- Spec 10 §3.1 — une contribution = une photo prise en direct, rattachée à un feu.
-- fire_event_id / hotspot_raw_id sont des clés LOGIQUES vers la socle (base séparée →
-- pas de FK dure). Position exacte de l'auteur JAMAIS stockée : seule distance_km (scalaire).
CREATE TABLE contribution (
    id               INTEGER PRIMARY KEY,
    public_id        TEXT UNIQUE,        -- opaque, assigné à la PUBLICATION (URL de l'image)
    fire_event_id    INTEGER,            -- feu socle rattaché (clé logique)
    hotspot_raw_id   INTEGER,            -- ancre géométrique (hotspot le plus proche)
    distance_km      REAL,               -- distance géoloc-live → hotspot au déclic (audit, scalaire)
    captured_at      TEXT NOT NULL,      -- instant de la prise de vue, horodaté SERVEUR (fraîcheur, §0)
    image_path       TEXT,               -- JPEG d'affichage (max_px), hors répertoire public (NULL après purge)
    thumb_path       TEXT,               -- JPEG vignette (thumb_px) pour la grille (NULL après purge)
    image_sha256     TEXT NOT NULL,      -- empreinte image d'affichage — dédup + traçabilité (survit à la purge)
    largeur          INTEGER,
    hauteur          INTEGER,
    thumb_largeur    INTEGER,
    thumb_hauteur    INTEGER,
    email            TEXT,               -- OPTIONNEL, non vérifié (prévenir de la publication) ; purgé à terme
    ip_hash          TEXT,               -- HMAC salé de l'IP (anti-abus/blacklist, jamais en clair)
    consentement_at  TEXT NOT NULL,      -- preuve de consentement (RGPD/LCEN)
    cgu_version      TEXT NOT NULL,      -- version des CGU/mentions acceptées (opposabilité)
    code_insee       TEXT,               -- commune CONTENANT le hotspot (point-dans-polygone) — optionnel (§7.4)
    statut           TEXT NOT NULL DEFAULT 'soumise'
        CHECK (statut IN ('soumise','auto_rejetee','a_moderer','publiee','rejetee','purgee')),
    score_nsfw       REAL,               -- auto-filtre (§5)
    score_feu        REAL,
    auto_verdict     TEXT,               -- ok / nsfw / hors_sujet
    auto_json        TEXT,               -- détail des scores (audit)
    moteur_auto      TEXT,               -- versions modèles (reproductibilité)
    moderee_par      TEXT,               -- 'admin' ou 'mail' (LCEN)
    motif_rejet      TEXT,               -- motif de rejet (LCEN)
    created_at       TEXT NOT NULL,      -- écriture de la ligne (= dépôt)
    moderee_at       TEXT,
    publiee_at       TEXT,
    purge_prevue_at  TEXT,
    purgee_at        TEXT
);

-- Anti-doublon (Spec 10 §3.4) : même image re-soumise = no-op ; une rejetée/purgée garde son
-- hash dans le squelette → non re-soumissible (anti-re-spam voulu).
CREATE UNIQUE INDEX idx_contribution_sha ON contribution (image_sha256);
-- File de modération (§6).
CREATE INDEX idx_contribution_statut ON contribution (statut);
-- Widget fiche feu (§7) : les publiées d'un feu, plus récentes en tête.
CREATE INDEX idx_contribution_feu ON contribution (fire_event_id, captured_at DESC);
-- Widget page commune (§7.4) : les publiées d'une commune, plus récentes en tête.
CREATE INDEX idx_contribution_commune ON contribution (code_insee, captured_at DESC);

-- Spec 10 §3.3 / §8 — blacklist IP (intérêt légitime anti-abus). IP hachée, blocage BORNÉ.
CREATE TABLE ip_blocklist (
    ip_hash    TEXT PRIMARY KEY,         -- HMAC salé (jamais l'IP en clair)
    motif      TEXT,
    source     TEXT NOT NULL CHECK (source IN ('manuel','auto')),
    cree_at    TEXT NOT NULL,            -- ISO UTC
    expire_at  TEXT                      -- ISO UTC ; blocage borné (révisable)
);

INSERT INTO schema_version (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
