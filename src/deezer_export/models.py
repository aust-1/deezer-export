"""Modèles de données communs à la ligne de commande et au site."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class Track:
    """Une piste de playlist, quelle que soit l'API qui l'a fournie."""

    title: str
    artist: str
    album: str
    album_id: int
    duration: int  # en secondes
    link: str


@dataclass(frozen=True, slots=True)
class PlaylistInfo:
    """Les métadonnées d'une playlist."""

    id: int
    title: str
    nb_tracks: int
    public: bool
    owner: str
    link: str
    picture: str | None = None  # pochette 250 px


@dataclass(frozen=True, slots=True)
class User:
    """Le compte Deezer connecté."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class PlaylistExport:
    """Le résultat d'une récupération : playlist, pistes et genres des albums."""

    playlist: PlaylistInfo
    tracks: tuple[Track, ...]
    genres: Mapping[int, tuple[str, ...]]
    generated_at: datetime
    albums_queried: int = field(default=0)

    def genres_of(self, track: Track) -> tuple[str, ...]:
        """Renvoie les genres de l'album d'une piste.

        Args:
            track (Track): La piste concernée.

        Returns:
            tuple[str, ...]: Les genres de son album (vide si inconnus).
        """
        return self.genres.get(track.album_id, ())
