"""Erreurs renvoyées par les clients Deezer.

Les messages sont en français et destinés à être affichés tels quels, en ligne de
commande comme sur le site : ils ne contiennent jamais de jeton ni de cookie.
"""


class DeezerError(Exception):
    """Erreur générique : Deezer injoignable ou réponse inattendue."""


class PlaylistNotFoundError(DeezerError):
    """La playlist n'existe pas (ou plus)."""


class PrivatePlaylistError(DeezerError):
    """La playlist existe mais n'est pas lisible avec les droits actuels."""


class AuthenticationError(DeezerError):
    """Jeton OAuth ou cookie « arl » refusé ou expiré."""


class QuotaExceededError(DeezerError):
    """Deezer limite les appels : réessayer un peu plus tard."""
