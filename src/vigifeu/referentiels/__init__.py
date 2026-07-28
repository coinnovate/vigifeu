"""Importers de référentiels (Lot 3) : communes (Admin Express) et BDIFF.

Ces modules ne sont PAS des fetchers réseau : ils lisent des fichiers livrés par
millésime (gros fichiers France entière, hors repo — plan §1.2). Les fixtures de
test (Gironde-ouest) sont des extraits minuscules committés, fabriqués depuis
geo.api.gouv.fr. L'import est idempotent (upsert par clé stable) : rejouer un
millésime met à jour, ne duplique jamais.
"""
