# Mise en ligne — bêta privée (Lot 5, phase A)

Procédure VPS pour servir le site en **bêta privée non indexée**, une fois les
commits « Lot 5 (1..2) » sur `main`. Le daemon génère désormais le site lui-même
(câblage Spec 04 §3) ; il n'y a plus rien à générer « à la main ».

Rappels prod (gotchas Lot 3) : le CLI n'est pas sur le PATH de root, la base et les
fichiers appartiennent à l'utilisateur `vigifeu`, `uv` est à `/usr/local/bin/uv`.

## 1. Confirmer le bug threading (avant/après)

Le daemon n'écrivait plus rien après le cycle de boot (jobs planifiés en erreur
cross-thread, capturée par APScheduler). Vérifier l'historique **avant** de déployer :

```bash
sudo journalctl -u vigifeu --since "-3d" | grep -iE "created in a thread|raised an exception" | head
```

Des lignes ici = bug confirmé en prod (la collecte ne tournait qu'au boot).

## 2. Clé MapTiler dans l'environnement systemd

```bash
sudo -u vigifeu tee -a /opt/vigifeu/.env >/dev/null <<'EOF'
VIGIFEU_MAPTILER_KEY=xxxxxxxxxxxxxxxx
EOF
```

(Voir `.env.example` pour la liste complète. La clé n'entre que dans
`data/site/static/carte-config.js`, jamais dans le HTML ni le dépôt.)

## 3. Déployer

```bash
sudo /opt/vigifeu/deploy/update.sh
```

`update.sh` fait : pull + `uv sync` + restart. Au redémarrage, le daemon (corrigé)
exécute dans l'ordre : `sync_static` → premier cycle fetch+moteur → **drain de
l'arriéré `regen_queue`** (accumulé depuis le Lot 3) → `finalize_site`. Le site est
donc construit dans `/opt/vigifeu/data/site` dès ce boot.

Vérifier que la collecte 15 min **repart réellement** (plus d'erreur cross-thread) et
que la génération a tourné :

```bash
sudo journalctl -u vigifeu -f | grep -E "fetch_firms|regen|finalize_site|created in a thread"
ls -la /opt/vigifeu/data/site            # index.html, feux/, communes/, sitemap*.xml, robots.txt
```

## 4. Droits de lecture pour Nginx

`data/` appartient à `vigifeu` ; Nginx (www-data) doit pouvoir lire le site :

```bash
sudo chmod -R a+rX /opt/vigifeu/data/site
```

## 5. Nginx — bêta privée (basic auth + noindex)

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-sentifeu <utilisateur>   # apache2-utils si absent
sudo cp /opt/vigifeu/deploy/nginx-sentifeu-beta.conf /etc/nginx/sites-available/sentifeu-beta
sudo ln -s /etc/nginx/sites-available/sentifeu-beta /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d beta.sentifeu.fr    # TLS (l'auth HTTP passe en clair sans HTTPS)
```

DNS : `beta.sentifeu.fr` → IP du VPS. Cloudflare **n'est pas** mis en place à ce stade
(décision cadrage : au passage public seulement).

## 6. Revue

Ouvrir `https://beta.sentifeu.fr` (auth) et relire : accueil + carte (fond MapTiler,
marqueurs), fiche feu Saumos, boucle feu↔commune, pages éditoriales. La fiche Saumos
doit rester conforme au golden (garde-fou CI). Puis on ouvre la phase B (exploitation).
