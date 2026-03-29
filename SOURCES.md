# Documentation complète — Bibliothèque EPUB de Marius

## Vue d'ensemble

Application web personnelle hébergée sur **Railway.app** permettant de chercher et télécharger des livres EPUB depuis un iPad (ou n'importe quel navigateur). Le PC de l'utilisateur n'est pas impliqué — tout tourne sur Railway 24h/24.

**URL du site :** à compléter (domaine Railway personnalisé)
**Repo GitHub :** https://github.com/Maryuus/bibliotheque-epub
**Hébergement :** Railway.app (plan gratuit avec crédits mensuels)

---

## Architecture

```
iPad (navigateur)
    │
    ▼
Railway (Flask + Tor)
    ├── Recherche ──► z-lib.cv  (IP Railway acceptée)
    └── Téléchargement ──► Tor ──► libgen.li  (IP Railway bloquée, Tor contourne)
```

### Pourquoi Tor ?
Les sites de piratage (libgen, Anna's Archive) bloquent les IPs des datacenters cloud (AWS/GCP/Railway). Tor fait passer les requêtes par des IPs résidentielles qui ne sont pas bloquées.

---

## Flux complet

### Recherche
1. L'utilisateur tape un titre/auteur sur le site
2. Railway scrape `z-lib.cv/s?q=...&extension=epub`
3. Parse les éléments `.resItemBox` : titre (`h3 a`), auteur, couverture (`img[data-src]`), langue
4. Retourne les résultats triés (langue préférée en premier)

### Téléchargement
1. L'utilisateur clique sur un livre
2. Le frontend appelle `/api/download?title=...&author=...`
3. Railway cherche le livre sur `libgen.li/index.php?req=...&ext=epub` **via Tor**
4. Trouve un lien `get.php?md5=XXX` ou `ads.php?md5=XXX` dans les résultats
5. Si `ads.php` : Railway fetche cette page via Tor pour trouver le vrai lien `get.php`
6. Retourne l'URL au frontend
7. Le frontend appelle `/api/proxy?url=...`
8. Railway télécharge le fichier EPUB depuis libgen **via Tor** et le stream vers l'iPad

---

## Fichiers du projet

```
/home/marius/projet/livres/
├── app.py              # Serveur Flask principal
├── templates/
│   └── index.html      # Interface mobile (HTML/CSS/JS vanilla)
├── requirements.txt    # Dépendances Python
├── Dockerfile          # Image Docker (installe Tor + Python)
├── Procfile            # Commande gunicorn (fallback Railway)
├── README.md           # Disclaimer légal
└── SOURCES.md          # Ce fichier
```

---

## Variables à changer si une URL change

Dans `app.py`, lignes 14-15 :

```python
ZLIB_BASE = "https://z-lib.cv"    # ← Changer si z-lib change de domaine
LIBGEN_BASE = "https://libgen.li" # ← Changer si libgen change de domaine
```

Après modification : `git add app.py && git commit -m "Update URLs" && git push`
Railway redéploie automatiquement en ~2 minutes.

### Comment trouver la nouvelle URL
- Pour Z-Library : cherche "Z-Library new domain" sur Reddit (r/zlibrary)
- Pour Libgen : cherche "Libgen mirror" sur Reddit (r/piracy)

---

## Sources testées depuis Railway

| Source | Statut | Usage |
|--------|--------|-------|
| z-lib.cv | ✅ Accessible | Recherche de livres |
| libgen.li | ❌ Bloqué directement | Accessible via Tor uniquement |
| libgen.rs | ❌ Timeout | Inaccessible |
| Anna's Archive | ❌ JS challenge | Inaccessible automatiquement |
| library.lol | ❌ Timeout | Inaccessible |

---

## Endpoints de l'API

| Endpoint | Usage |
|----------|-------|
| `GET /` | Page principale |
| `GET /api/search?title=...&author=...&lang=fr` | Recherche de livres |
| `GET /api/download?title=...&author=...` | Trouve le lien EPUB sur libgen via Tor |
| `GET /api/proxy?url=...` | Proxifie le téléchargement vers l'iPad |
| `GET /api/tor-test` | Vérifie que Tor fonctionne (debug) |
| `GET /api/test` | Teste l'accessibilité des sources (debug) |
| `GET /api/debug` | Inspecte une page z-lib (debug) |

---

## Déploiement

### Déploiement automatique
Tout push sur la branche `master` GitHub déclenche un redéploiement automatique sur Railway.

### Si Railway ne redéploie pas automatiquement
Railway → service → Deployments → vérifier que GitHub est connecté dans Settings → Source.

### Variables d'environnement Railway
| Variable | Usage |
|----------|-------|
| `PORT` | Port du serveur (géré automatiquement par Railway) |
| `ZLIB_SESSION` | Cookie de session z-lib (optionnel, non utilisé actuellement) |

---

## Comportement au démarrage

1. Gunicorn démarre immédiatement → site accessible
2. Tor démarre en thread background → prend ~30-60 secondes
3. Si l'utilisateur clique "Télécharger" avant que Tor soit prêt → message "Connexion sécurisée en cours… Réessaie dans 30 secondes"

---

## Structure de index.html

- **Header sticky** : logo + formulaire de recherche (titre, auteur, langue FR/EN)
- **Grille de résultats** : cartes avec couverture, titre, auteur, langue
- **Panneau de téléchargement** : s'ouvre au clic sur une carte, appelle `/api/download` puis `/api/proxy`
- Interface **mobile-first**, dark mode, CSS custom (pas de framework)

---

## Problèmes connus et solutions

| Problème | Cause | Solution |
|----------|-------|----------|
| "Introuvable sur Libgen" | Livre absent de libgen ou Tor pas encore prêt | Attendre 30s et réessayer, ou le livre n'existe pas sur libgen |
| "Connexion sécurisée en cours" | Tor en train de s'initialiser | Attendre 30-60s après démarrage du serveur |
| Recherche retourne 0 résultats | z-lib.cv a changé d'URL ou de structure HTML | Vérifier `/api/test`, mettre à jour `ZLIB_BASE` |
| 502 Bad Gateway | App crashée (erreur Python) | Vérifier les logs Railway → Deployments |

---

## Scraping z-lib.cv — sélecteurs CSS

Si z-lib change de structure HTML, voici les sélecteurs à mettre à jour dans `search_zlib()` :

```python
soup.select(".resItemBox")              # Conteneur de chaque résultat
item.select_one("h3[itemprop='name'] a, h3 a")  # Titre
item.select_one("a[href^='/book/']")    # Lien vers la page du livre
item.select_one(".authors, .itemAuthors, [itemprop='author']")  # Auteur
item.select_one("img.cover, img[data-src]")  # Couverture (attribut data-src)
```

## Scraping libgen.li — sélecteurs CSS

Si libgen change de structure, voici les sélecteurs à mettre à jour dans `find_epub_url_tor()` :

```python
soup.select("table.table-striped tr")  # Tableau des résultats
soup.select("a[href*='get.php']")      # Lien de téléchargement direct
soup.select("a[href*='ads.php?md5']")  # Page intermédiaire (fallback)
```
