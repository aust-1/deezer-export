"""Ce que le site demande à Deezer, derrière une interface remplaçable en test."""

from __future__ import annotations

from typing import TYPE_CHECKING

from deezer_export.deezer.gateway import GatewayClient
from deezer_export.deezer.oauth import authorize_url, exchange_code
from deezer_export.deezer.public import PublicClient
from deezer_export.service import fetch_playlist, make_source

if TYPE_CHECKING:
    from collections.abc import Callable

    from deezer_export.models import PlaylistExport, PlaylistInfo, User
    from deezer_export.web.config import Settings
    from deezer_export.web.sessions import DeezerAuth


class DeezerBackend:
    """Accès réel à Deezer."""

    def __init__(self, settings: Settings) -> None:
        """Initialise l'accès.

        Args:
            settings (Settings): Les réglages du site.
        """
        self._settings = settings

    @staticmethod
    def fetch(
        playlist_id: int,
        auth: DeezerAuth | None,
        progress: Callable[[str, int, int], None],
    ) -> PlaylistExport:
        """Récupère une playlist complète.

        Args:
            playlist_id (int): L'ID de la playlist.
            auth (DeezerAuth | None): L'identité Deezer de la session.
            progress (Callable[[str, int, int], None]): Rappel d'avancement.

        Returns:
            PlaylistExport: La playlist, ses pistes et leurs genres.
        """
        source, public = make_source(**_credentials(auth))
        return fetch_playlist(playlist_id, source, public, progress=progress)

    @staticmethod
    def my_playlists(auth: DeezerAuth) -> list[PlaylistInfo]:
        """Liste les playlists du compte connecté.

        Args:
            auth (DeezerAuth): L'identité Deezer de la session.

        Returns:
            list[PlaylistInfo]: Ses playlists.
        """
        source, _ = make_source(**_credentials(auth))
        return source.my_playlists()

    @staticmethod
    def login_arl(arl: str) -> User:
        """Vérifie un cookie « arl ».

        Args:
            arl (str): La valeur du cookie.

        Returns:
            User: Le compte correspondant.
        """
        return GatewayClient(arl).login()

    def authorize_url(self, state: str) -> str:
        """Construit l'URL d'autorisation OAuth.

        Args:
            state (str): Valeur aléatoire liée à la session (anti-CSRF).

        Returns:
            str: L'URL de la page d'autorisation Deezer.
        """
        redirect = f"{self._settings.oauth_redirect_uri}?state={state}"
        return authorize_url(self._settings.deezer_app_id or "", redirect)

    def login_oauth(self, code: str) -> tuple[str, int | None, User]:
        """Échange un code OAuth contre un jeton et identifie le compte.

        Args:
            code (str): Le code reçu sur l'URL de retour.

        Returns:
            tuple[str, int | None, User]: Le jeton, sa durée de validité en
            secondes (None si illimitée) et le compte.
        """
        token = exchange_code(
            self._settings.deezer_app_id or "",
            self._settings.deezer_app_secret or "",
            code,
        )
        user = PublicClient(access_token=token.access_token).me()
        return token.access_token, token.expires_in, user


def _credentials(auth: DeezerAuth | None) -> dict[str, str | None]:
    if auth is None:
        return {"arl": None, "access_token": None}
    if auth.kind == "arl":
        return {"arl": auth.secret, "access_token": None}
    return {"arl": None, "access_token": auth.secret}
