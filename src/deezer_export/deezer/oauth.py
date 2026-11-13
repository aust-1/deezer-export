"""Connexion OAuth 2.0 à Deezer (flux « authorization code »).

Nécessite une application déclarée sur https://developers.deezer.com/myapps
(identifiant + clé secrète). Deezer ne permet plus d'en créer de nouvelles depuis
octobre 2025 : ce mode ne sert qu'à qui en possède déjà une. Sinon, voir
`deezer_export.deezer.gateway` (cookie « arl »).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode

import requests

from deezer_export.deezer.errors import AuthenticationError, DeezerError
from deezer_export.deezer.http import TIMEOUT, make_session

CONNECT = "https://connect.deezer.com/oauth"
# basic_access suffit pour lire ses propres playlists, privées comprises.
PERMS = ("basic_access",)


@dataclass(frozen=True, slots=True)
class OAuthToken:
    """Jeton d'accès renvoyé par Deezer."""

    access_token: str = field(repr=False)
    expires_in: int | None  # secondes ; None = sans expiration


def authorize_url(app_id: str, redirect_uri: str) -> str:
    """Construit l'URL de la page d'autorisation Deezer.

    Args:
        app_id (str): L'identifiant de l'application Deezer.
        redirect_uri (str): L'URL de retour, déclarée dans l'application.

    Returns:
        str: L'URL vers laquelle rediriger l'utilisateur.
    """
    query = urlencode({
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "perms": ",".join(PERMS),
    })
    return f"{CONNECT}/auth.php?{query}"


def exchange_code(
    app_id: str,
    secret: str,
    code: str,
    session: requests.Session | None = None,
) -> OAuthToken:
    """Échange le code d'autorisation contre un jeton d'accès.

    Args:
        app_id (str): L'identifiant de l'application Deezer.
        secret (str): La clé secrète de l'application.
        code (str): Le code reçu sur l'URL de retour.
        session (requests.Session | None): Session HTTP à réutiliser.

    Returns:
        OAuthToken: Le jeton d'accès.

    Raises:
        AuthenticationError: Code refusé ou expiré.
        DeezerError: Deezer injoignable.
    """
    http = session or make_session()
    try:
        resp = http.get(
            f"{CONNECT}/access_token.php",
            params={"app_id": app_id, "secret": secret, "code": code, "output": "json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        msg = "Deezer est injoignable pour le moment, réessaie plus tard."
        raise DeezerError(msg) from exc

    data: dict[str, object]
    try:
        parsed = resp.json()
        data = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        # Sans output=json, Deezer répond « access_token=…&expires=… ».
        data = {k: v[0] for k, v in parse_qs(resp.text).items()}

    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        msg = "Deezer a refusé la connexion : recommence depuis le début."
        raise AuthenticationError(msg)

    expires = int(str(data.get("expires", 0)) or 0)
    return OAuthToken(access_token=token, expires_in=expires or None)
