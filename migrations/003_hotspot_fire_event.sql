-- Vigifeu — migration 003 : rattachement courant hotspot → FireEvent (Lot 2)
--
-- Le clustering incrémental (Spec 02 §4) a besoin de retrouver, pour chaque
-- nouveau hotspot, les FireEvents candidats et leur géométrie. On matérialise
-- donc la membership COURANTE d'un hotspot par une colonne fire_event_id sur
-- hotspot_raw.
--
-- Comme overpass_id et fixed_source_id (migration 001), fire_event_id est un
-- attribut d'INTERPRÉTATION, nullable, sans contrainte FK déclarée : le poser ou
-- le recalculer ne viole pas l'immuabilité des observations (P1) et reste
-- entièrement recalculable depuis hotspot_raw (P2). La table immuable et déjà
-- peuplée en production n'est jamais recréée : simple ADD COLUMN additif.
--
-- Distinction avec fe_hotspot : cette dernière lie une VERSION de feu à ses
-- hotspots (avec dedup_group), pour la relecture historique ; fire_event_id est
-- l'état courant, indexé, interrogé à chaque passage.

ALTER TABLE hotspot_raw ADD COLUMN fire_event_id INTEGER;   -- Lot 2 (comme overpass_id)
CREATE INDEX idx_hotspot_fire_event ON hotspot_raw (fire_event_id);

INSERT INTO schema_version (version, applied_at)
VALUES (3, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
