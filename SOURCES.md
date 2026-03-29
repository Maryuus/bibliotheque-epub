# Sources et configuration

Ce fichier documente les sources utilisées par l'application et comment les mettre à jour.

## Sources actuelles

| Variable dans `app.py` | URL actuelle | Rôle |
|---|---|---|
| `ZLIB_BASE` | `https://z-lib.id` | Recherche et téléchargement de livres EPUB |

## Comment mettre à jour une URL

Si une source change d'adresse, ouvre `app.py` et modifie la constante en haut du fichier :

```python
ZLIB_BASE = "https://z-lib.id"   # ← changer ici
```

Puis commit et push :
```bash
git add app.py
git commit -m "Update ZLIB_BASE URL"
git push
```

Railway redéploie automatiquement.

## Pourquoi les URLs changent

Les sites de livres comme Z-Library ou Anna's Archive changent régulièrement de domaine pour éviter les blocages. Si la recherche retourne 0 résultats, c'est probablement que l'URL a changé.

Pour trouver la nouvelle URL :
- Cherche "Z-Library new domain" ou "Anna's Archive new domain" sur Reddit (r/zlibrary, r/Piracy)
- Ou consulte https://en.wikipedia.org/wiki/Z-Library pour l'URL officielle actuelle

## Sources testées et leur statut depuis Railway

| Source | Statut | Notes |
|---|---|---|
| z-lib.id | ✅ Fonctionne | Utilisée actuellement |
| z-lib.cv | ✅ Accessible | Alternative si .id tombe |
| libgen.li | ❌ Bloqué (503) | Bloque les IPs datacenter |
| libgen.rs | ❌ Timeout | Inaccessible depuis Railway |
| annas-archive.gs | ❌ JS challenge | Bloque les bots |

## Fonctionnement technique

1. **Recherche** : `GET /api/search?title=...&author=...&lang=fr`
   → Scrape z-lib.id/s?q=... et parse les `.resItemBox`

2. **Téléchargement** : `GET /api/download?url=https://z-lib.id/book/...`
   → Ouvre la page du livre et cherche les liens de téléchargement

3. **Proxy** : `GET /api/proxy?url=...`
   → Proxifie le fichier EPUB pour que l'iPad le télécharge directement

## Déploiement

- Hébergé sur Railway.app
- Repo GitHub : https://github.com/Maryuus/bibliotheque-epub
- Tout push sur `master` déclenche un redéploiement automatique
