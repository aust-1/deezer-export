# deezer-export

Exporte une playlist Deezer vers un classeur Excel : une feuille détaillée piste
par piste, et une feuille de statistiques (indicateurs globaux, classements et
graphique des genres).

Les données sont récupérées depuis l'[API publique Deezer](https://developers.deezer.com/api),
sans authentification.

## Fonctionnalités

- Récupération de **toutes** les pistes d'une playlist (pagination automatique).
- Enrichissement par les **genres** de l'album de chaque piste (un appel par
  album, mis en cache pour éviter les doublons).
- Export dans un classeur `.xlsx` avec vrais tableaux Excel : filtres, lignes
  alternées, en-tête figé et colonnes ajustées au contenu.

## Contenu du classeur

### Feuille `Playlist`

Une ligne par piste :

| Colonne               | Description                                      |
| --------------------- | ------------------------------------------------ |
| `title`               | Titre de la piste                                |
| `artist`              | Artiste principal                                |
| `album`               | Album d'où provient la piste                     |
| `duration_s`          | Durée en secondes                                |
| `link`                | Lien Deezer de la piste                          |
| `genre 1` … `genre 4` | Genres de l'album (colonnes vides si moins de 4) |

### Feuille `Statistiques`

- **Vue d'ensemble** : nombre de pistes, d'artistes, d'albums et de genres
  uniques, durée totale et moyenne, pistes la plus longue et la plus courte,
  date de génération de l'export.
- **Top artistes**, **Top albums**, **Top genres** : les 10 premiers de chaque
  classement, avec le nombre de pistes et la part correspondante.
- **Graphique** : diagramme en barres des genres les plus représentés.

Les statistiques sont recalculées à chaque export à partir des pistes
récupérées : aucune donnée n'est conservée entre deux exécutions.

## Prérequis

- Python ≥ 3.13.7
- [uv](https://docs.astral.sh/uv/) (recommandé) pour gérer l'environnement

## Installation

```bash
git clone https://github.com/aust-1/deezer-export.git
cd deezer-export
uv sync
```

## Utilisation

L'ID de playlist et le chemin de sortie sont définis en bas de
[`src/main.py`](src/main.py) :

```python
export_playlist(
    "10016300382",
    f"output/playlist_{dt.now(PARIS).date().isoformat()}.xlsx",
)
```

Remplacez l'ID par le vôtre, puis lancez :

```bash
mkdir -p output
uv run python src/main.py
```

```text
50 pistes exportées vers output/playlist_2026-08-26.xlsx (48 albums interrogés)
```

La fonction peut aussi être appelée depuis votre propre code :

```python
from main import export_playlist

export_playlist("908622995", "ma_playlist.xlsx")
```

### Trouver l'ID d'une playlist

Il s'agit du nombre présent dans l'URL Deezer :

```text
https://www.deezer.com/fr/playlist/908622995
                                   ^^^^^^^^^
```

## Limites

- **La playlist doit être publique.** L'API renvoie une `OAuthException` pour
  une playlist privée, qui nécessiterait un jeton OAuth.
- Un appel HTTP est effectué par album distinct : l'export d'une grande
  playlist prend un certain temps. L'API Deezer limite à environ 50 requêtes
  toutes les 5 secondes par IP et le script n'applique aucun ralentissement.
- Seul l'artiste principal de chaque piste est exporté.

## Développement

Les outils sont configurés dans [`pyproject.toml`](pyproject.toml) :

```bash
uv run ruff check src        # lint + format
uv run pyright src           # typage
uv run pylint src            # analyse statique
uv run pytest                # tests
```

## Licence

[MIT](LICENSE) — Eliott A. Roussille
