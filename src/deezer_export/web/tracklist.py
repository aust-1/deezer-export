"""Liste des pistes de la page de résultats : filtre, tri et pagination côté serveur.

Tout passe par la chaîne de requête (``?q=…&tri=…&page=…``) : la liste fonctionne sans
JavaScript, et une grande playlist ne rend plus que 100 lignes à la fois.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from deezer_export.models import PlaylistExport, Track

PAGE_SIZE = 100
QUERY_MAX_LENGTH = 100
_MAX_PAGE_DIGITS = 9
ANCHOR = "pistes"  # id de la section, pour revenir à la liste après navigation
DEFAULT_SORT = "num"
# Tri accepté → (colonne, décroissant).
SORTS = {
    "num": ("num", False),
    "-num": ("num", True),
    "duree": ("duree", False),
    "-duree": ("duree", True),
}


@dataclass(frozen=True, slots=True)
class TrackRow:
    """Une ligne du tableau : la piste, sa position dans la playlist et ses genres."""

    number: int  # position dans la playlist, à partir de 1
    track: Track
    genres: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrackPage:
    """Une page de la liste des pistes, après filtre et tri."""

    rows: tuple[TrackRow, ...]
    page: int
    pages: int
    matched: int  # pistes retenues par le filtre
    total: int  # pistes de la playlist
    query: str
    sort: str

    @property
    def first(self) -> int:
        """Rang (parmi les pistes retenues) de la première ligne de la page.

        Returns:
            int: Le rang, à partir de 1 (0 si la page est vide).
        """
        return (self.page - 1) * PAGE_SIZE + 1 if self.rows else 0

    @property
    def last(self) -> int:
        """Rang (parmi les pistes retenues) de la dernière ligne de la page.

        Returns:
            int: Le rang, à partir de 1 (0 si la page est vide).
        """
        return self.first + len(self.rows) - 1 if self.rows else 0

    def url(self, *, page: int | None = None, sort: str | None = None) -> str:
        """Construit le lien vers une autre page ou un autre tri, filtre conservé.

        Changer de tri revient à la première page.

        Args:
            page (int | None): La page voulue (défaut : la page courante).
            sort (str | None): Le tri voulu (défaut : le tri courant).

        Returns:
            str: Le lien relatif, par exemple ``?q=rock&page=2#pistes``.
        """
        target_sort = self.sort if sort is None else sort
        target_page = (1 if sort is not None else self.page) if page is None else page
        params: dict[str, str | int] = {}
        if self.query:
            params["q"] = self.query
        if target_sort != DEFAULT_SORT:
            params["tri"] = target_sort
        if target_page > 1:
            params["page"] = target_page
        return f"?{urlencode(params)}#{ANCHOR}"

    def sort_url(self, column: str) -> str:
        """Lien de tri d'une colonne : croissant, ou inversé si elle est déjà triée.

        Args:
            column (str): ``num`` ou ``duree``.

        Returns:
            str: Le lien relatif.
        """
        current, descending = SORTS[self.sort]
        if current == column and not descending:
            return self.url(sort=f"-{column}")
        return self.url(sort=column)

    def aria_sort(self, column: str) -> str:
        """Valeur de l'attribut ``aria-sort`` d'une colonne triable.

        Args:
            column (str): ``num`` ou ``duree``.

        Returns:
            str: ``ascending``, ``descending`` ou ``none``.
        """
        current, descending = SORTS[self.sort]
        if current != column:
            return "none"
        return "descending" if descending else "ascending"


def track_page(
    export: PlaylistExport,
    *,
    page: str | int = 1,
    query: str = "",
    sort: str = DEFAULT_SORT,
) -> TrackPage:
    """Filtre, trie et découpe la liste des pistes d'un export.

    Les valeurs venues de l'URL sont tolérées : page illisible ou hors limites ramenée
    à la plus proche, tri inconnu remplacé par l'ordre de la playlist.

    Args:
        export (PlaylistExport): L'export affiché.
        page (str | int): La page demandée, à partir de 1.
        query (str): Le filtre (titre, artiste, album ou genre ; casse et accents
            ignorés).
        sort (str): Le tri : ``num``, ``-num``, ``duree`` ou ``-duree``.

    Returns:
        TrackPage: La page à afficher.
    """
    query = " ".join(query.split())[:QUERY_MAX_LENGTH]
    sort = sort if sort in SORTS else DEFAULT_SORT
    rows = [
        TrackRow(number=n, track=t, genres=export.genres_of(t))
        for n, t in enumerate(export.tracks, start=1)
    ]
    if query:
        words = _fold(query).split()
        rows = [r for r in rows if all(w in _haystack(r) for w in words)]

    column, descending = SORTS[sort]
    if column == "duree":  # à durée égale, l'ordre de la playlist
        rows.sort(key=lambda r: (r.track.duration, r.number), reverse=descending)
    elif descending:
        rows.reverse()

    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    number = min(max(_int(page), 1), pages)
    start = (number - 1) * PAGE_SIZE
    return TrackPage(
        rows=tuple(rows[start : start + PAGE_SIZE]),
        page=number,
        pages=pages,
        matched=len(rows),
        total=len(export.tracks),
        query=query,
        sort=sort,
    )


def _haystack(row: TrackRow) -> str:
    t = row.track
    return _fold(" ".join((t.title, t.artist, t.album, *row.genres)))


def _fold(text: str) -> str:
    """Minuscules sans accents : « Électro » et « electro » se valent.

    Args:
        text (str): Le texte.

    Returns:
        str: Le texte normalisé.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    text = value.strip()
    return int(text) if text.isdigit() and len(text) <= _MAX_PAGE_DIGITS else 1
