"""Client de la passerelle interne de Deezer (gw-light), authentifié par cookie « arl ».

C'est l'API qu'utilise le lecteur web de Deezer. Elle n'est pas documentée, mais
c'est le seul moyen de lire ses playlists privées sans application OAuth (Deezer
n'en accepte plus de nouvelles depuis octobre 2025).

Le cookie « arl » donne accès à tout le compte : ce client ne le journalise jamais,
ne l'écrit nulle part et ne l'envoie qu'à www.deezer.com, en HTTPS.

Les genres ne sont pas fournis ici : ils viennent de l'API publique (les albums
sont publics), voir `deezer_export.service`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    QuotaExceededError,
)
from deezer_export.deezer.http import TIMEOUT, RateLimiter, make_session
from deezer_export.deezer.public import BLOCKED_MESSAGE, HTTP_BLOCKED
from deezer_export.models import PlaylistInfo, Track, User

if TYPE_CHECKING:
    from collections.abc import Iterator

GATEWAY = "https://www.deezer.com/ajax/gw-light.php"
PAGE_SIZE = 1000
COVERS = "https://cdn-images.dzcdn.net/images"
_TOKEN_ERRORS = (
    {"GATEWAY_ERROR": "invalid api token"},
    {"VALID_TOKEN_REQUIRED": "Invalid CSRF token"},
)
_STATUS_PUBLIC = 0
_NO_TOKEN = "null"  # ruff: ignore[hardcoded-password-string] - pas un secret

GATEWAY_LIMITER = RateLimiter(max_calls=20, period=5.0)


class GatewayClient:
    """Lecture des playlists d'un compte Deezer via son cookie « arl »."""

    def __init__(
        self,
        arl: str,
        *,
        session: requests.Session | None = None,
        limiter: RateLimiter = GATEWAY_LIMITER,
    ) -> None:
        """Initialise le client, sans encore contacter Deezer.

        Args:
            arl (str): La valeur du cookie « arl » de deezer.com.
            session (requests.Session | None): Session HTTP à réutiliser.
            limiter (RateLimiter): Limiteur de débit.
        """
        self._session = session or make_session()
        self._session.cookies.set("arl", arl.strip(), domain=".deezer.com", secure=True)
        self._limiter = limiter
        self._api_token: str | None = None
        self._user: User | None = None

    def __repr__(self) -> str:  # n'affiche jamais le cookie
        """Représentation sans secret.

        Returns:
            str: La représentation du client.
        """
        return f"GatewayClient(user={self._user!r})"

    def login(self) -> User:
        """Vérifie le cookie et récupère le jeton de session de la passerelle.

        Returns:
            User: Le compte correspondant au cookie.

        Raises:
            AuthenticationError: Cookie invalide ou expiré.
        """
        results = self._call("deezer.getUserData", api_token=_NO_TOKEN)
        user = results.get("USER") or {}
        user_id = int(user.get("USER_ID") or 0)
        if not user_id:
            msg = "Ce cookie arl n'ouvre aucune session Deezer (invalide ou expiré)."
            raise AuthenticationError(msg)
        self._api_token = str(results.get("checkForm", ""))
        self._user = User(id=user_id, name=str(user.get("BLOG_NAME", "")))
        return self._user

    @property
    def user(self) -> User:
        """Le compte connecté (connexion à la demande).

        Returns:
            User: Le compte connecté.
        """
        return self._user or self.login()

    def playlist(self, playlist_id: int) -> PlaylistInfo:
        """Lit les métadonnées d'une playlist accessible à ce compte.

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            PlaylistInfo: Ses métadonnées.
        """
        page = self.call(
            "deezer.pagePlaylist",
            {"PLAYLIST_ID": str(playlist_id), "lang": "fr", "header": True, "tab": 0},
        )
        return playlist_from_gateway(page.get("DATA") or {})

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Lit toutes les pistes d'une playlist, par pages de 1 000.

        Args:
            playlist_id (int): L'ID de la playlist.

        Returns:
            list[Track]: Les pistes, dans l'ordre de la playlist.
        """
        return [track_from_gateway(s) for s in self._songs(playlist_id)]

    def my_playlists(self) -> list[PlaylistInfo]:
        """Liste les playlists du compte, privées comprises.

        Returns:
            list[PlaylistInfo]: Ses playlists.
        """
        user = self.user
        page = self.call(
            "deezer.pageProfile",
            {"USER_ID": str(user.id), "tab": "playlists", "nb": 2000},
        )
        items = ((page.get("TAB") or {}).get("playlists") or {}).get("data") or []
        return [playlist_from_gateway(p, default_owner=user.name) for p in items]

    def call(self, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Appelle une méthode de la passerelle au nom du compte connecté.

        Args:
            method (str): La méthode, par exemple ``playlist.getSongs``.
            args (dict[str, Any] | None): Ses arguments.

        Returns:
            dict[str, Any]: Le champ ``results`` de la réponse.

        Raises:
            AuthenticationError: Session refusée.
        """
        if self._api_token is None:
            self.login()
        try:
            return self._call(method, args, api_token=self._api_token or "")
        except _TokenExpiredError:
            self.login()  # le jeton CSRF expire : on le renouvelle une fois
            try:
                return self._call(method, args, api_token=self._api_token or "")
            except _TokenExpiredError as exc:
                msg = "Deezer refuse la session : reconnecte-toi."
                raise AuthenticationError(msg) from exc

    def _songs(self, playlist_id: int) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            page = self.call(
                "playlist.getSongs",
                {"PLAYLIST_ID": str(playlist_id), "start": start, "nb": PAGE_SIZE},
            )
            items = page.get("data") or []
            yield from items
            start += len(items)
            if not items or start >= int(page.get("total") or 0):
                return

    def _call(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        api_token: str,
    ) -> dict[str, Any]:
        self._limiter.acquire()
        params = {
            "method": method,
            "input": "3",
            "api_version": "1.0",
            "api_token": api_token,
        }
        try:
            resp = self._session.post(
                GATEWAY, params=params, json=args or {}, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            msg = "Deezer est injoignable pour le moment, réessaie plus tard."
            raise DeezerError(msg) from exc
        if resp.status_code in HTTP_BLOCKED:
            raise QuotaExceededError(BLOCKED_MESSAGE)
        try:
            payload = resp.json()
        except ValueError as exc:  # requests.JSONDecodeError en hérite aussi
            msg = f"Réponse illisible de Deezer (HTTP {resp.status_code})."
            raise DeezerError(msg) from exc

        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            raise _gateway_error(error)
        results = payload.get("results")
        return results if isinstance(results, dict) else {}


class _TokenExpiredError(DeezerError):
    """Jeton CSRF de la passerelle expiré (renouvelé automatiquement)."""


def _gateway_error(error: dict[str, Any] | list[Any]) -> DeezerError:
    if error in _TOKEN_ERRORS:
        return _TokenExpiredError("jeton expiré")
    keys = set(error) if isinstance(error, dict) else set()
    if keys & {"PERMISSION_ERROR", "NOT_ALLOWED"}:
        return PrivatePlaylistError(
            "Cette playlist est privée et n'appartient pas à ton compte."
        )
    if keys & {"DATA_ERROR", "NOT_FOUND"}:
        return PlaylistNotFoundError("Playlist introuvable sur Deezer.")
    if keys & {"USER_AUTH_REQUIRED", "REQUEST_ERROR"}:
        return AuthenticationError("Deezer refuse la session : reconnecte-toi.")
    return DeezerError("Deezer a refusé la requête.")


def track_from_gateway(data: dict[str, Any]) -> Track:
    """Convertit une piste de la passerelle (champs ``SNG_*``, ``ALB_*``…).

    Le titre est complété par la version (« Remastered 2015 »…), comme dans l'API
    publique.

    Args:
        data (dict[str, Any]): La piste telle que renvoyée par la passerelle.

    Returns:
        Track: La piste normalisée.
    """
    title = str(data.get("SNG_TITLE", ""))
    version = str(data.get("VERSION") or "").strip()
    if version:
        title = f"{title} {version}"
    return Track(
        title=title,
        artist=str(data.get("ART_NAME", "")),
        album=str(data.get("ALB_TITLE", "")),
        album_id=int(data.get("ALB_ID") or 0),
        duration=int(data.get("DURATION") or 0),
        link=f"https://www.deezer.com/track/{data.get('SNG_ID')}",
    )


def playlist_from_gateway(
    data: dict[str, Any], default_owner: str = ""
) -> PlaylistInfo:
    """Convertit une playlist de la passerelle (champs ``PLAYLIST_ID``, ``TITLE``…).

    Args:
        data (dict[str, Any]): La playlist telle que renvoyée par la passerelle.
        default_owner (str): Le propriétaire à afficher s'il n'est pas fourni.

    Returns:
        PlaylistInfo: La playlist normalisée.
    """
    playlist_id = int(data.get("PLAYLIST_ID") or 0)
    picture = data.get("PLAYLIST_PICTURE")
    kind = data.get("PICTURE_TYPE") or "playlist"
    return PlaylistInfo(
        id=playlist_id,
        title=str(data.get("TITLE", "")),
        nb_tracks=int(data.get("NB_SONG") or 0),
        public=int(data.get("STATUS") or 0) == _STATUS_PUBLIC,
        owner=str(data.get("PARENT_USERNAME") or default_owner),
        link=f"https://www.deezer.com/playlist/{playlist_id}",
        picture=f"{COVERS}/{kind}/{picture}/250x250-000000-80-0-0.jpg"
        if picture
        else None,
    )
