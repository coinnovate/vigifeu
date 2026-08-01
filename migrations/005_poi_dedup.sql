-- Vigifeu — migration 005 : déduplication inter-sources du référentiel POI (Spec 06 §2.3)
--
-- Étape 8 (Spec 06 §6) : les 2ᵉ/3ᵉ sources (BD TOPO, Géorisques) recouvrent OSM — un même
-- camping présent dans OSM ET BD TOPO ne doit compter qu'une fois (§2.3). On DÉDUPLIQUE
-- sans jamais supprimer (P1) : chaque POi doublon pointe vers son représentant canonique
-- via superseded_by ; les lignes gardent leur provenance (source, source_ref, imported_at).
--
-- superseded_by = NULL  → POI canonique (compté sur les fiches, indexé pour les relations) ;
-- superseded_by = id    → doublon d'un autre POI (exclu de l'index feu↔POI et du recensement
--                         commune↔POI, qui filtrent superseded_by IS NULL).
--
-- Métadonnée DÉRIVÉE, recalculée par engine.relations.recompute_poi_dedup (déterministe,
-- idempotente, indépendante de l'ordre d'import) — comme commune_poi est recalculée à
-- l'import. Ce n'est pas une observation : la réécrire ne viole pas P1 (aucune donnée perdue).

ALTER TABLE poi ADD COLUMN superseded_by INTEGER REFERENCES poi(id);
CREATE INDEX idx_poi_superseded ON poi (superseded_by);

INSERT INTO schema_version (version, applied_at)
VALUES (5, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
