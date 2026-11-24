"""Client de l'API publique Deezer (api.deezer.com), avec ou sans jeton OAuth.

Sans jeton, seules les playlists publiques sont lisibles. Avec un jeton obtenu par
OAuth (voir `deezer_export.deezer.oauth`), les playlists privées de l'utilisateur
connecté le sont aussi.

Deezer signale ses erreurs dans le corps JSON, avec un statut HTTP 200 :
``{"error": {"type": "...", "message": "...", "code": 800}}``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, NoReturn

import requests

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    QuotaExceededError,
)
from deezer_export.deezer.http import TIMEOUT, RateLimiter, make_session
from deezer_export.models import PlaylistInfo, Track, User

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

API = "https://api.deezer.com"
PAGE_SIZE = 100

# Codes d'erreur documentés par Deezer.
QUOTA = 4
PERMISSION = 200
TOKEN_INVALID = 300
SERVICE_BUSY = 700
DATA_NOT_FOUND = 800
_RETRYABLE = {QUOTA, SERVICE_BUSY}
_HTTP_SERVER_ERROR = 500
# Le CDN de Deezer (Akamai) répond 403 ou 429, en HTML, à une IP trop insistante.
HTTP_BLOCKED = frozenset({403, 429})
QUOTA_MESSAGE = "Deezer limite les appels : réessaie dans une minute."
BLOCKED_MESSAGE = (
    "Deezer bloque temporairement les requêtes de ce serveur (trop d'appels) : "
    "réessaie dans quelques minutes."
)

# Partagé par tous les clients du processus : le quota Deezer est par adresse IP.
PUBLIC_LIMITER = RateLimiter()


class PublicClient:
    """Accès à api.deezer.com, avec limitation de débit et reprises automatiques."""

    def __init__(
        self,
        access_token: str | None = None,
        *,
        session: requests.Session | None = None,
        limiter: RateLimiter = PUBLIC_LIMITER,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
    ) -> None:
        """Initialise le client.

        Args:
            access_token (str | None): Jeton OAuth de l'utilisateur, s'il est connecté.
            session (requests.Session | None): Session HTTP à réutiliser.
            limiter (RateLimiter): Limiteur de débit (partagé par défaut).
            sleep (Callable[[float], None]): Fonction d'attente entre deux reprises.
            max_retries (int): Nombre de reprises sur quota dépassé ou erreur réseau.
        """
        self._token = access_token
        self._session = session or make_session()
        self._limiter = limiter
        self._sleep = sleep
        self._max_retries = max_retries

    @property
    def authenticated(self) -> bool:
        """Indique si le client agit au nom d'un utilisateur connecté.

        Returns:
            bool: Vrai si un jeton OAuth est utilisé.
        """
        return self._token is not None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Appelle l'API et renvoie la réponse JSON, erreurs Deezer converties.

        Args:
            path (str): Chemin de la ressource, par exemple ``/playlist/123``.
            params (dict[str, Any] | None): Paramètres de requête.

        Returns:
            dict[str, Any]: Le corps JSON de la réponse.

        Raises:
            DeezerError: Deezer injoignable ou réponse inexploitable, ou toute
                autre erreur signalée par Deezer.
            QuotaExceededError: Quota dépassé, ou adresse IP bloquée, trop longtemps.
        """
        query = dict(params or {})
        if self._token:
            query["access_token"] = self._token

        for attempt in range(self._max_retries + 1):
            last_attempt = attempt == self._max_retries
            self._limiter.acquire()
            try:
                resp = self._session.get(f"{API}{path}", params=query, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if last_attempt:
                    msg = "Deezer est injoignable pour le moment, réessaie plus tard."
                    raise DeezerError(msg) from exc
                self._backoff(attempt)
                continue

            if resp.status_code in HTTP_BLOCKED:
                if last_attempt:
                    raise QuotaExceededError(BLOCKED_MESSAGE)
                self._backoff(attempt)
                continue
            if resp.status_code >= _HTTP_SERVER_ERROR and not last_attempt:
                self._backoff(attempt)
                continue
            payload = _json(resp)

            error = payload.get("error")
            if not error:
                return payload
            if error.get("code") in _RETRYABLE and not last_attempt:
                self._backoff(attempt)
                continue
            self._raise_for(error)

        raise QuotaExceededError(
            QUOTA_MESSAGE
        )  # pragma: no cover - la boucle renvoie ou lève

    def paginate(self, path: str) -> Iterator[dict[str, Any]]:
        """Parcourt toutes les pages d'une collection (``data`` + ``next``).

        Args:
            path (str): Chemin de la collection.

        Yields:
            dict[str, Any]: Chaque élément de la collection, dans l'ordre.
        """
        index = 0
        while True:
            page = self.get(path, {"limit": PAGE_SIZE, "index": index})
            items = page.get("data", [])
            yield from items
            index += len(items)
            if not items or not page.get("next"):
                return

    def playlist(self, playlist_id: int) -> PlaylistInfo:
        """Lit les métadonnées d'une playlist.

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            PlaylistInfo: Ses métadonnées.
        """
        return playlist_from_api(self.get(f"/playlist/{playlist_id}"))

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Lit toutes les pistes d'une playlist (pagination automatique).

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            list[Track]: Les pistes, dans l'ordre de la playlist.
        """
        return [
            track_from_api(t) for t in self.paginate(f"/playlist/{playlist_id}/tracks")
        ]

    def album_genres(self, album_id: int) -> tuple[str, ...]:
        """Lit les genres d'un album.

        Args:
            album_id (int): L'ID de l'album.

        Returns:
            tuple[str, ...]: Ses genres (vide si l'album n'existe plus).
        """
        try:
            album = self.get(f"/album/{album_id}")
        except PlaylistNotFoundError:
            return ()
        return tuple(g["name"] for g in album.get("genres", {}).get("data", []))

    def me(self) -> User:
        """Lit le compte connecté.

        Returns:
            User: L'utilisateur propriétaire du jeton.
        """
        data = self.get("/user/me")
        return User(id=int(data["id"]), name=str(data.get("name", "")))

    def my_playlists(self) -> list[PlaylistInfo]:
        """Liste les playlists du compte connecté, privées comprises.

        Returns:
            list[PlaylistInfo]: Ses playlists.
        """
        return [playlist_from_api(p) for p in self.paginate("/user/me/playlists")]

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(2.0**attempt, 30.0))

    def _raise_for(self, error: dict[str, Any]) -> NoReturn:
        code = error.get("code")
        exc: DeezerError
        if code in _RETRYABLE:
            exc = QuotaExceededError(QUOTA_MESSAGE)
        elif code == DATA_NOT_FOUND:
            hint = "" if self.authenticated else " Si elle est privée, connecte-toi."
            exc = PlaylistNotFoundError(f"Playlist introuvable sur Deezer.{hint}")
        elif code == TOKEN_INVALID:
            exc = AuthenticationError(
                "Ta session Deezer a expiré : reconnecte-toi pour continuer."
            )
        elif code == PERMISSION or error.get("type") == "OAuthException":
            exc = PrivatePlaylistError(
                "Cette playlist est privée et n'appartient pas à ton compte."
                if self.authenticated
                else "Cette playlist est privée : connecte-toi pour l'exporter."
            )
        else:
            message = error.get("message") or "erreur inconnue"
            exc = DeezerError(f"Deezer a refusé la requête ({message}).")
        raise exc


def _json(resp: requests.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        msg = f"Réponse illisible de Deezer (HTTP {resp.status_code})."
        raise DeezerError(msg) from exc
    if not isinstance(payload, dict):
        msg = "Réponse inattendue de Deezer."
        raise DeezerError(msg)
    return payload


def track_from_api(data: dict[str, Any]) -> Track:
    """Convertit une piste de l'API publique.

    Args:
        data (dict[str, Any]): La piste telle que renvoyée par Deezer.

    Returns:
        Track: La piste normalisée.
    """
    return Track(
        title=str(data["title"]),
        artist=str(data["artist"]["name"]),
        album=str(data["album"]["title"]),
        album_id=int(data["album"]["id"]),
        duration=int(data["duration"]),
        link=str(data.get("link") or f"https://www.deezer.com/track/{data['id']}"),
    )


def playlist_from_api(data: dict[str, Any]) -> PlaylistInfo:
    """Convertit une playlist de l'API publique.

    Args:
        data (dict[str, Any]): La playlist telle que renvoyée par Deezer.

    Returns:
        PlaylistInfo: La playlist normalisée.
    """
    playlist_id = int(data["id"])
    return PlaylistInfo(
        id=playlist_id,
        title=str(data.get("title", "")),
        nb_tracks=int(data.get("nb_tracks", 0)),
        public=bool(data.get("public", True)),
        owner=str((data.get("creator") or {}).get("name", "")),
        link=str(data.get("link") or f"https://www.deezer.com/playlist/{playlist_id}"),
        picture=data.get("picture_medium") or None,
    )
