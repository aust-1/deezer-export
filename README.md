# deezer-export

Exporte une playlist Deezer, **publique ou privée**, vers un classeur Excel : une feuille
détaillée piste par piste, et une feuille de statistiques (indicateurs globaux, classements et
graphique des genres). Deux façons de s'en servir :

- **en ligne de commande** : `deezer-export <playlist>` ;
- **sur le web** : un site qui affiche les statistiques, suit l'avancement de l'export et
  propose le classeur au téléchargement. Il reprend la charte graphique ER\_ v2 (celle du
  portfolio).

Projet personnel, non affilié à Deezer.

## Fonctionnalités

- Récupération de **toutes** les pistes d'une playlist (pagination automatique).
- Enrichissement par les **genres** de l'album de chaque piste : une requête par album
  distinct, mise en cache.
- **Playlists privées** : connexion par OAuth (si tu as une application Deezer) ou par le
  cookie de session `arl` (voir [Playlists privées](#playlists-privées)).
- **Respect du quota Deezer** : environ 50 requêtes toutes les 5 secondes par adresse IP.
  Le débit est limité à 45 requêtes par fenêtre, avec reprise automatique si Deezer signale un
  quota dépassé ou bloque temporairement l'adresse IP.
- Classeur `.xlsx` avec de vrais tableaux Excel : filtres, lignes alternées, en-tête figé et
  colonnes ajustées au contenu.
- Messages d'erreur explicites : playlist introuvable, privée, session expirée, Deezer
  injoignable…

## Contenu du classeur

### Feuille `Playlist`

Une ligne par piste :

| Colonne               | Description                                      |
| --------------------- | ------------------------------------------------ |
| `title`               | Titre de la piste (version comprise)             |
| `artist`              | Artiste principal                                |
| `album`               | Album d'où provient la piste                     |
| `duration_s`          | Durée en secondes                                |
| `link`                | Lien Deezer de la piste                          |
| `genre 1` … `genre 4` | Genres de l'album (colonnes vides si moins de 4) |

### Feuille `Statistiques`

- **Vue d'ensemble** :
  - nom de la playlist ;
  - nombre de pistes, d'artistes, d'albums et de genres uniques ;
  - durée totale et durée moyenne ;
  - piste la plus longue et piste la plus courte ;
  - date de génération de l'export.
- **Top artistes**, **Top albums**, **Top genres** : les 10 premiers de chaque classement, avec
  le nombre de pistes et la part correspondante. Deux albums homonymes d'artistes différents
  sont comptés séparément.
- **Graphique** : diagramme en barres des genres les plus représentés.

## Prérequis

- Python ≥ 3.13.7
- [uv](https://docs.astral.sh/uv/)
- Docker, pour déployer le site

## Installation

```bash
git clone https://github.com/aust-1/deezer-export.git
cd deezer-export
uv sync
cp .env.example .env   # puis compléter les valeurs utiles
```

## Ligne de commande

```bash
uv run deezer-export 908622995
uv run deezer-export https://www.deezer.com/fr/playlist/908622995 -o ma_playlist.xlsx
uv run deezer-export                 # playlist lue dans DEEZER_PLAYLIST_ID
uv run deezer-export --mine          # liste tes playlists (connexion requise)
```

```text
Exportation de la playlist Deezer vers un fichier Excel…
50 pistes exportées vers output/playlist_908622995_2026-11-13.xlsx (49 albums interrogés)
```

- **Playlist** : un ID, un lien `deezer.com/…/playlist/<id>` ou un lien de partage court
  (`link.deezer.com/…`).
- **Fichier de sortie** : par défaut `output/playlist_<id>_<date>.xlsx`, daté du jour (fuseau
  `Europe/Paris`). Le dossier est créé au besoin.
- **Codes de sortie** :
  - 0 : succès ;
  - 1 : erreur Deezer ou référence invalide ;
  - 2 : argument manquant.

Pour trouver l'ID d'une playlist, prends le nombre à la fin de son URL Deezer :

```text
https://www.deezer.com/fr/playlist/908622995
                                   ^^^^^^^^^
```

La fonction est aussi utilisable depuis ton propre code :

```python
from deezer_export import export_playlist

export_playlist("908622995", "ma_playlist.xlsx")
export_playlist(123456, "privee.xlsx", arl="…")  # playlist privée
```

### Variables (`.env`)

| Variable              | Rôle                                                       |
| --------------------- | ---------------------------------------------------------- |
| `DEEZER_PLAYLIST_ID`  | Playlist exportée quand aucun argument n'est donné         |
| `DEEZER_ARL`          | Cookie `arl`, pour les playlists privées                   |
| `DEEZER_ACCESS_TOKEN` | Jeton OAuth d'une application Deezer (alternative à `arl`) |

Les variables de l'environnement prennent le pas sur le fichier `.env`.

## Playlists privées

L'API publique de Deezer ne donne accès qu'aux playlists publiques. Pour lire les siennes,
deux moyens existent.

### 1. OAuth (si tu as déjà une application Deezer)

C'est la voie officielle, mais **Deezer n'accepte plus de nouvelles applications depuis
octobre 2025**. Si tu en as une :

1. Renseigne `DEEZER_APP_ID` et `DEEZER_APP_SECRET` (site), ou `DEEZER_ACCESS_TOKEN` (CLI).
2. Déclare l'URL de retour `$PUBLIC_URL/connexion/deezer/retour` dans l'application.

Seule la permission `basic_access` est demandée.

### 2. Cookie `arl`

C'est le cookie de session du lecteur web Deezer. L'outil interroge alors la passerelle
interne de Deezer (`gw-light`), comme le fait le lecteur.

1. Ouvre [deezer.com](https://www.deezer.com) et connecte-toi.
2. Ouvre les outils de développement (`F12`) :
   - Chrome : onglet **Application**, puis **Cookies** → `https://www.deezer.com` ;
   - Firefox : onglet **Stockage**, puis **Cookies** → `https://www.deezer.com`.
3. Copie la valeur de `arl` dans `DEEZER_ARL` (CLI), ou colle-la dans le formulaire du site.

> [!WARNING]
> Le cookie `arl` donne accès à **tout le compte**. Ne le partage qu'avec un serveur de
> confiance. Pour le révoquer, déconnecte-toi de Deezer sur tous les appareils. Cette
> passerelle n'est pas documentée par Deezer et peut changer sans préavis.

Ce que fait le site du cookie (ou du jeton OAuth) :

- il le garde **en mémoire seulement**, côté serveur, le temps de la session : jamais sur
  disque, jamais dans les journaux ;
- il ne l'envoie qu'à `www.deezer.com`, en HTTPS ;
- il l'oublie à la déconnexion, après 60 minutes d'inactivité ou au redémarrage du service.

Le navigateur ne reçoit qu'un identifiant de session aléatoire (cookie `HttpOnly`,
`SameSite=Lax`, `Secure` en HTTPS), renouvelé à chaque connexion.

Les genres viennent toujours de l'API publique : les albums sont publics.

## Site web

```bash
uv run deezer-export-web          # http://127.0.0.1:8000 (HOST et PORT modifiables)
```

1. On colle un ID ou un lien de playlist, ou on choisit l'une de ses playlists une fois
   connecté.
2. L'export tourne en tâche de fond. La page suit l'avancement : en JS, ou par
   rafraîchissement automatique sans JS.
3. La page de résultat affiche :
   - les chiffres clés, la vue d'ensemble et les genres ;
   - les classements et la liste des pistes ;
   - le bouton de téléchargement du classeur.

Le résultat reste disponible 30 minutes, puis il est oublié. Une grande playlist prend
environ une minute pour 500 albums distincts, à cause du quota Deezer.

### Sécurité

- **Contenu chargé** : CSP stricte (`default-src 'none'`). Seuls le site lui-même et les
  pochettes du CDN Deezer sont autorisés : polices auto-hébergées, aucun script externe, sauf
  les statistiques Rybbit si elles sont configurées.
- **Formulaires** : un jeton CSRF par session.
- **Exports** : chacun n'est visible que par la session qui l'a lancé.
- **Limites** : un export actif par session, et `MAX_PENDING_JOBS` exports en cours ou en
  attente au plus.
- **En-têtes** : `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`,
  `Cache-Control: no-store` sur les pages, et HSTS en HTTPS.

### Variables du site

| Variable                                | Défaut                  | Rôle                                                  |
| --------------------------------------- | ----------------------- | ----------------------------------------------------- |
| `PUBLIC_URL`                            | `http://127.0.0.1:8000` | URL publique (cookies `Secure` si `https://`, OAuth)  |
| `DEEZER_APP_ID`, `DEEZER_APP_SECRET`    | —                       | Application Deezer : active la connexion OAuth        |
| `ARL_LOGIN`                             | `true`                  | Active la connexion par cookie `arl`                  |
| `SESSION_TTL_MINUTES`                   | `60`                    | Inactivité avant expiration d'une session             |
| `EXPORT_WORKERS`                        | `2`                     | Exports simultanés                                    |
| `MAX_PENDING_JOBS`                      | `8`                     | Exports en cours ou en attente au plus                |
| `ANALYTICS_SCRIPT`, `ANALYTICS_SITE_ID` | —                       | Statistiques de visite Rybbit (facultatif)            |
| `HOST`, `PORT`                          | `127.0.0.1`, `8000`     | Adresse d'écoute                                      |
| `FORWARDED_ALLOW_IPS`                   | `127.0.0.1`             | Proxys de confiance pour `X-Forwarded-*`              |

## Déploiement (Docker, VPS)

Le dépôt suit le contrat des apps du VPS
([aust-1/vps-strasbourg-opt](https://github.com/aust-1/vps-strasbourg-opt),
`docs/add-a-service.md`) :

- **`docker-compose.yml`** :
  - projet `deezer-export`, aucun port publié ;
  - réseau externe `proxy`, alias `deezer-export-web` ;
  - conteneur en lecture seule, `cap_drop: ALL`, `no-new-privileges` ;
  - journaux tournants, healthcheck sur `/healthz`.
- **`Caddyfile`** : route `deezer.eliott-roussille.fr` vers `deezer-export-web:8000`.
- **`.env.example`** : toutes les variables, sans secret.
- **`Dockerfile`** : image `python:3.13-slim`, dépendances figées par `uv.lock`, utilisateur
  non root.

```bash
# sur le VPS, dans /opt
scripts/app-add.sh deezer-export https://github.com/aust-1/deezer-export.git
nano apps/deezer-export/.env
scripts/app-deploy.sh deezer-export
```

Le workflow **Déploiement** (`.github/workflows/deploy.yml`) redéploie l'app après chaque CI
verte sur `main`. Il reste inactif tant que la variable de dépôt `DEPLOY_ENABLED` ne vaut pas
`true`, et il lui faut les secrets `VPS_HOST`, `VPS_SSH_KEY` et `VPS_KNOWN_HOSTS` (voir
`docs/ci-deploy.md` du dépôt opt).

En local, sans le proxy du VPS :

```bash
docker network create proxy 2>/dev/null || true
docker compose up -d --build
```

## Développement

```bash
uv run pytest                     # tests (sans réseau : Deezer est simulé) + couverture
uv run ruff format src tests      # format
uv run ruff check src tests       # lint
uv run pydoclint src              # docstrings (style Google)
uv run mypy src tests             # typage
uv run pyright
uv run pylint src                 # analyse statique (consultative)
```

La CI (`.github/workflows/ci.yml`) lance tout cela, puis construit l'image Docker et vérifie
qu'elle démarre saine, en lecture seule comme sur le VPS.

### Structure

```text
src/deezer_export/
├── deezer/        clients Deezer : API publique (+ OAuth), passerelle arl, limiteur, erreurs
├── models.py      Track, PlaylistInfo, PlaylistExport…
├── service.py     références de playlist, récupération complète, cache des genres
├── stats.py       statistiques communes au classeur et au site
├── excel.py       construction du classeur
├── cli.py         ligne de commande
└── web/           site : FastAPI, sessions et exports en mémoire, gabarits, charte ER_
```

## Limites

- Seul l'artiste principal de chaque piste est exporté.
- Les genres sont ceux de l'album, pas de la piste.
- Les sessions et les exports du site vivent en mémoire : un redémarrage les efface. C'est
  voulu, rien de sensible n'est écrit sur disque.

## Licence

[MIT](LICENSE) — Eliott A. Roussille. Les polices embarquées (Inter, Inter Tight,
JetBrains Mono) sont sous licence [SIL OFL 1.1](src/deezer_export/web/static/fonts/OFL.txt).
