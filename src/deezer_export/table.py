"""Colonnes de la feuille « Playlist », communes au classeur Excel et au CSV."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from deezer_export.models import PlaylistExport

GENRE_COLUMNS = 4
PLAYLIST_HEADER = (
    "title",
    "artist",
    "album",
    "duration_s",
    "link",
    "added_at",
    "rank",
    "explicit",
    "isrc",
    *(f"genre {i}" for i in range(1, GENRE_COLUMNS + 1)),
)
ADDED_AT_COLUMN = PLAYLIST_HEADER.index("added_at") + 1  # 1-based, pour Excel

type Cell = str | int | bool | datetime | None


def playlist_rows(export: PlaylistExport) -> list[tuple[Cell, ...]]:
    """Construit les lignes de la feuille « Playlist », une par piste.

    Les valeurs gardent leur type (entier, booléen, date) ; une valeur inconnue vaut
    None (cellule vide). La date d'ajout est à l'heure de Paris, sans fuseau : Excel
    n'en gère pas.

    Args:
        export (PlaylistExport): La playlist récupérée.

    Returns:
        list[tuple[Cell, ...]]: Les lignes, dans l'ordre de ``PLAYLIST_HEADER``.
    """
    rows: list[tuple[Cell, ...]] = []
    for t in export.tracks:
        genres = [*export.genres_of(t), *[""] * GENRE_COLUMNS][:GENRE_COLUMNS]
        added_at = t.added_at.replace(tzinfo=None) if t.added_at else None
        rows.append((
            t.title,
            t.artist,
            t.album,
            t.duration,
            t.link,
            added_at,
            t.rank,
            t.explicit,
            t.isrc,
            *genres,
        ))
    return rows
