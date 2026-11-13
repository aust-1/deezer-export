"""Statistiques d'une playlist, communes au classeur Excel et au site."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deezer_export.models import PlaylistExport, Track

TOP_N = 10
NO_GENRE = "(sans genre)"


@dataclass(frozen=True, slots=True)
class RankingRow:
    """Une ligne de classement."""

    name: str
    count: int
    share: float  # entre 0 et 1


@dataclass(frozen=True, slots=True)
class PlaylistStats:
    """Indicateurs globaux et classements d'une playlist."""

    track_count: int
    artist_count: int
    album_count: int
    genre_count: int
    total_duration: int
    average_duration: int
    longest: Track | None
    shortest: Track | None
    top_artists: tuple[RankingRow, ...]
    top_albums: tuple[RankingRow, ...]
    top_genres: tuple[RankingRow, ...]


def compute_stats(export: PlaylistExport, top_n: int = TOP_N) -> PlaylistStats:
    """Calcule les statistiques d'une playlist.

    Les albums sont distingués par leur ID (deux « Greatest Hits » d'artistes
    différents comptent pour deux) et affichés par leur titre. Une piste dont
    l'album n'a aucun genre compte dans « (sans genre) ».

    Args:
        export (PlaylistExport): La playlist récupérée.
        top_n (int): Taille des classements.

    Returns:
        PlaylistStats: Les statistiques.
    """
    tracks = export.tracks
    artists = Counter(t.artist for t in tracks)
    albums_by_id = Counter(t.album_id for t in tracks)
    album_titles = {t.album_id: t.album for t in tracks}
    genres: Counter[str] = Counter()
    for t in tracks:
        genres.update(set(export.genres_of(t)) or {NO_GENRE})

    total = sum(t.duration for t in tracks)
    albums = Counter[str]()
    for album_id, count in albums_by_id.most_common():
        albums[_unique_title(album_titles[album_id], albums)] = count

    return PlaylistStats(
        track_count=len(tracks),
        artist_count=len(artists),
        album_count=len(albums_by_id),
        genre_count=len(genres),
        total_duration=total,
        average_duration=total // len(tracks) if tracks else 0,
        longest=max(tracks, key=lambda t: t.duration, default=None),
        shortest=min(tracks, key=lambda t: t.duration, default=None),
        top_artists=_ranking(artists, top_n),
        top_albums=_ranking(albums, top_n),
        top_genres=_ranking(genres, top_n),
    )


def _unique_title(title: str, taken: Counter[str]) -> str:
    """Renvoie un titre d'album non encore utilisé dans le classement.

    Args:
        title (str): Le titre de l'album.
        taken (Counter[str]): Les titres déjà classés.

    Returns:
        str: Le titre, suffixé de « (2) », « (3) »… en cas d'homonyme.
    """
    candidate, n = title, 1
    while candidate in taken:
        n += 1
        candidate = f"{title} ({n})"
    return candidate


def _ranking(counts: Counter[str], top_n: int) -> tuple[RankingRow, ...]:
    total = sum(counts.values())
    return tuple(
        RankingRow(name=name, count=count, share=count / total)
        for name, count in counts.most_common(top_n)
    )


def format_duration(seconds: int) -> str:
    """Formate une durée : « 3 min 05 s » ou « 2 h 29 min 54 s ».

    Args:
        seconds (int): La durée en secondes.

    Returns:
        str: La durée lisible.
    """
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {secs:02d} s"
    return f"{minutes} min {secs:02d} s"


def describe_track(track: Track | None) -> str:
    """Décrit une piste sur une ligne : titre, artiste et durée.

    Args:
        track (Track | None): La piste, ou None.

    Returns:
        str: La description, ou « — » sans piste.
    """
    if track is None:
        return "—"
    return f"{track.title} — {track.artist} ({format_duration(track.duration)})"
