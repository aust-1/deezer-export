"""Récupération d'une playlist complète (pistes + genres), commune au CLI et au site."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace
from datetime import datetime as dt
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from deezer_export.deezer.gateway import GatewayClient
from deezer_export.deezer.http import TIMEOUT, make_session
from deezer_export.deezer.public import PublicClient
from deezer_export.excel import save_workbook
from deezer_export.models import PlaylistExport

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from deezer_export.models import PlaylistInfo, Track

PARIS = ZoneInfo("Europe/Paris")
# Liens de partage courts : seul cas où l'on suit une redirection, vers deezer.com.
SHORT_LINK_HOSTS = frozenset({"link.deezer.com", "deezer.page.link"})
_DEEZER_HOSTS = ("deezer.com", "www.deezer.com")
_PLAYLIST_PATH = re.compile(r"/playlist/(\d+)")
_DIGITS = re.compile(r"\d{1,20}")


class PlaylistSource(Protocol):
    """Ce qu'il faut savoir lire pour exporter une playlist."""

    def playlist(self, playlist_id: int) -> PlaylistInfo:
        """Lit les métadonnées d'une playlist.

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            PlaylistInfo: Ses métadonnées.
        """
        ...

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Lit les pistes d'une playlist.

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            list[Track]: Ses pistes.
        """
        ...

    def my_playlists(self) -> list[PlaylistInfo]:
        """Liste les playlists du compte connecté.

        Returns:
            list[PlaylistInfo]: Ses playlists.
        """
        ...


class InvalidPlaylistReferenceError(ValueError):
    """Le texte saisi n'est ni un ID ni un lien de playlist Deezer."""


class GenreCache:
    """Cache des genres par album, partagé entre exports (les albums sont publics)."""

    def __init__(self, ttl: float = 24 * 3600, max_size: int = 50_000) -> None:
        """Initialise un cache vide.

        Args:
            ttl (float): Durée de validité d'une entrée, en secondes.
            max_size (int): Nombre maximal d'albums gardés.
        """
        self._ttl = ttl
        self._max_size = max_size
        self._data: dict[int, tuple[float, tuple[str, ...]]] = {}
        self._lock = threading.Lock()

    def get(self, album_id: int) -> tuple[str, ...] | None:
        """Renvoie les genres en cache d'un album.

        Args:
            album_id (int): L'ID de l'album.

        Returns:
            tuple[str, ...] | None: Ses genres, ou None s'ils ne sont pas en cache.
        """
        with self._lock:
            entry = self._data.get(album_id)
            if entry is None or time.monotonic() - entry[0] > self._ttl:
                return None
            return entry[1]

    def put(self, album_id: int, genres: tuple[str, ...]) -> None:
        """Mémorise les genres d'un album.

        Args:
            album_id (int): L'ID de l'album.
            genres (tuple[str, ...]): Ses genres.
        """
        with self._lock:
            if len(self._data) >= self._max_size:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[album_id] = (time.monotonic(), genres)


GENRE_CACHE = GenreCache()


class ExportCache:
    """Exports récents de playlists **publiques**, partagés entre visiteurs.

    Une entrée est indexée par l'ID de la playlist et n'est réutilisée que si le
    ``checksum`` de sa liste de pistes n'a pas changé : vérifier coûte une requête,
    au lieu d'une par page de pistes. Les playlists privées n'y entrent jamais.
    """

    def __init__(self, ttl: float = 15 * 60, max_tracks: int = 100_000) -> None:
        """Initialise un cache vide.

        Args:
            ttl (float): Durée de validité d'une entrée, en secondes.
            max_tracks (int): Nombre total de pistes gardées (borne la mémoire).
        """
        self._ttl = ttl
        self._max_tracks = max_tracks
        self._data: dict[int, tuple[float, str, PlaylistExport]] = {}
        self._lock = threading.Lock()

    def get(self, playlist_id: int, checksum: str) -> PlaylistExport | None:
        """Renvoie l'export en cache d'une playlist, s'il est récent et à jour.

        Args:
            playlist_id (int): L'ID de la playlist.
            checksum (str): Le ``checksum`` actuel de sa liste de pistes.

        Returns:
            PlaylistExport | None: L'export, ou None s'il est absent, expiré ou si
            la playlist a changé depuis.
        """
        with self._lock:
            entry = self._data.get(playlist_id)
            if entry is None:
                return None
            stored_at, stored_checksum, export = entry
            if time.monotonic() - stored_at > self._ttl:
                del self._data[playlist_id]
                return None
            return export if stored_checksum == checksum else None

    def put(self, export: PlaylistExport) -> None:
        """Mémorise l'export d'une playlist publique dont le ``checksum`` est connu.

        Args:
            export (PlaylistExport): L'export (ignoré si la playlist est privée ou
                sans ``checksum``, ou trop grande pour le cache).
        """
        info = export.playlist
        size = len(export.tracks)
        if not info.public or not info.checksum or size > self._max_tracks:
            return
        with self._lock:
            self._data.pop(info.id, None)
            while self._data and self._tracks() + size > self._max_tracks:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[info.id] = (time.monotonic(), info.checksum, export)

    def __len__(self) -> int:
        """Nombre d'exports en cache.

        Returns:
            int: Le nombre d'entrées.
        """
        return len(self._data)

    def _tracks(self) -> int:
        return sum(len(e[2].tracks) for e in self._data.values())


EXPORT_CACHE = ExportCache()


def parse_playlist_reference(
    value: str,
    session: requests.Session | None = None,
) -> int:
    """Extrait l'ID d'une playlist depuis un ID, un lien Deezer ou un lien court.

    Args:
        value (str): Le texte saisi (``908622995``,
            ``https://www.deezer.com/fr/playlist/908622995``, lien de partage…).
        session (requests.Session | None): Session HTTP pour suivre un lien court.

    Returns:
        int: L'ID de la playlist.

    Raises:
        InvalidPlaylistReferenceError: Le texte ne désigne pas une playlist.
    """
    text = value.strip()
    if _DIGITS.fullmatch(text):
        return int(text)

    url = urlparse(text if "://" in text else f"https://{text}")
    host = (url.hostname or "").lower()
    if host in SHORT_LINK_HOSTS:
        url = urlparse(_resolve_short_link(url.geturl(), session))
        host = (url.hostname or "").lower()

    match = _PLAYLIST_PATH.search(url.path)
    if host in _DEEZER_HOSTS and match:
        return int(match.group(1))

    msg = "Colle l'ID d'une playlist Deezer ou son lien (deezer.com/playlist/…)."
    raise InvalidPlaylistReferenceError(msg)


def _resolve_short_link(url: str, session: requests.Session | None) -> str:
    http = session or make_session()
    try:
        resp = http.head(url, allow_redirects=True, timeout=TIMEOUT)
    except requests.RequestException as exc:
        msg = "Impossible de suivre ce lien de partage : colle plutôt le lien complet."
        raise InvalidPlaylistReferenceError(msg) from exc
    return resp.url


def make_source(
    *,
    arl: str | None = None,
    access_token: str | None = None,
) -> tuple[PlaylistSource, PublicClient]:
    """Choisit la source des pistes selon l'authentification disponible.

    Args:
        arl (str | None): Cookie « arl » (passerelle Deezer).
        access_token (str | None): Jeton OAuth (API publique authentifiée).

    Returns:
        tuple[PlaylistSource, PublicClient]: La source des pistes et le client
        utilisé pour les genres des albums.
    """
    public = PublicClient(access_token=access_token)
    if arl:
        return GatewayClient(arl), public
    return public, public


def fetch_playlist(
    playlist_id: int,
    source: PlaylistSource,
    genres_client: PublicClient,
    *,
    cache: GenreCache = GENRE_CACHE,
    exports: ExportCache | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    now: Callable[[], dt] = lambda: dt.now(PARIS),
) -> PlaylistExport:
    """Récupère une playlist, ses pistes et les genres de chaque album.

    Args:
        playlist_id (int): L'ID de la playlist.
        source (PlaylistSource): D'où lire la playlist et ses pistes.
        genres_client (PublicClient): Client de l'API publique pour les albums.
        cache (GenreCache): Cache des genres par album.
        exports (ExportCache | None): Cache des exports de playlists publiques, à
            ne passer que si ``source`` est l'API publique (même vue pour tous).
        progress (Callable[[str, int, int], None] | None): Rappel
            ``(étape, fait, total)`` appelé au fil de l'avancement.
        now (Callable[[], dt]): Horloge (injectable pour les tests).

    Returns:
        PlaylistExport: La playlist complète.
    """
    notify = progress or (lambda _step, _done, _total: None)
    notify("playlist", 0, 0)
    info = source.playlist(playlist_id)
    if exports is not None and info.public and info.checksum:
        recent = exports.get(info.id, info.checksum)
        if recent is not None:  # rien n'a changé : vérifié à l'instant
            return replace(recent, playlist=info, generated_at=now())
    notify("tracks", 0, info.nb_tracks)
    tracks = tuple(source.playlist_tracks(playlist_id))

    album_ids = list(dict.fromkeys(t.album_id for t in tracks if t.album_id))
    genres = _album_genres(album_ids, genres_client, cache, notify)

    export = PlaylistExport(
        playlist=info,
        tracks=tracks,
        genres=genres,
        generated_at=now(),
        albums_queried=len(album_ids),
    )
    if exports is not None:
        exports.put(export)
    return export


def _album_genres(
    album_ids: Iterable[int],
    client: PublicClient,
    cache: GenreCache,
    notify: Callable[[str, int, int], None],
) -> dict[int, tuple[str, ...]]:
    ids = list(album_ids)
    genres: dict[int, tuple[str, ...]] = {}
    notify("albums", 0, len(ids))
    for done, album_id in enumerate(ids, start=1):
        cached = cache.get(album_id)
        if cached is None:
            cached = client.album_genres(album_id)
            cache.put(album_id, cached)
        genres[album_id] = cached
        notify("albums", done, len(ids))
    return genres


def export_playlist(
    playlist: str | int,
    output: str | Path = "playlist.xlsx",
    *,
    arl: str | None = None,
    access_token: str | None = None,
) -> PlaylistExport:
    """Exporte une playlist Deezer vers un fichier Excel.

    Le classeur contient deux feuilles : « Playlist » (une ligne par piste) et
    « Statistiques » (indicateurs globaux et classements).

    Args:
        playlist (str | int): L'ID de la playlist, ou son lien Deezer.
        output (str | Path): Le chemin du fichier de sortie.
        arl (str | None): Cookie « arl », pour une playlist privée.
        access_token (str | None): Jeton OAuth, pour une playlist privée.

    Returns:
        PlaylistExport: Les données exportées.

    Les erreurs Deezer (`DeezerError`) et les références invalides
    (`InvalidPlaylistReferenceError`) remontent telles quelles.

    """
    playlist_id = (
        playlist if isinstance(playlist, int) else parse_playlist_reference(playlist)
    )
    source, genres_client = make_source(arl=arl, access_token=access_token)
    export = fetch_playlist(playlist_id, source, genres_client)
    save_workbook(export, output)
    return export
