"""Accès à Deezer : API publique (avec ou sans OAuth) et passerelle par cookie arl."""

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    QuotaExceededError,
)
from deezer_export.deezer.gateway import GatewayClient
from deezer_export.deezer.public import PublicClient

__all__ = [
    "AuthenticationError",
    "DeezerError",
    "GatewayClient",
    "PlaylistNotFoundError",
    "PrivatePlaylistError",
    "PublicClient",
    "QuotaExceededError",
]
