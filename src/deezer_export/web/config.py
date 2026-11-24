"""Configuration du site, lue depuis l'environnement (voir .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_PUBLIC_URL = "http://127.0.0.1:8000"
_TRUE = {"1", "true", "yes", "on", "oui"}


def _flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    """Réglages du site."""

    public_url: str = DEFAULT_PUBLIC_URL
    deezer_app_id: str | None = None
    deezer_app_secret: str | None = field(default=None, repr=False)
    arl_login: bool = True
    session_ttl_minutes: int = 60
    max_pending_jobs: int = 8
    workers: int = 2
    analytics_script: str | None = (
        None  # ex. https://analytics.exemple.fr/api/script.js
    )
    analytics_site_id: str | None = None

    @property
    def oauth_enabled(self) -> bool:
        """Indique si la connexion OAuth à Deezer est configurée.

        Returns:
            bool: Vrai si l'identifiant et la clé de l'application sont fournis.
        """
        return bool(self.deezer_app_id and self.deezer_app_secret)

    @property
    def secure_cookies(self) -> bool:
        """Indique si les cookies doivent porter l'attribut ``Secure``.

        Returns:
            bool: Vrai si le site est servi en HTTPS.
        """
        return self.public_url.startswith("https://")

    @property
    def oauth_redirect_uri(self) -> str:
        """L'URL de retour à déclarer dans l'application Deezer.

        Returns:
            str: L'URL de retour OAuth.
        """
        return f"{self.public_url.rstrip('/')}/connexion/deezer/retour"

    @classmethod
    def from_env(cls) -> Settings:
        """Lit les réglages depuis les variables d'environnement.

        Returns:
            Settings: Les réglages.
        """
        return cls(
            public_url=os.getenv("PUBLIC_URL", DEFAULT_PUBLIC_URL).strip().rstrip("/"),
            deezer_app_id=os.getenv("DEEZER_APP_ID", "").strip() or None,
            deezer_app_secret=os.getenv("DEEZER_APP_SECRET", "").strip() or None,
            arl_login=_flag("ARL_LOGIN", default=True),
            session_ttl_minutes=_int("SESSION_TTL_MINUTES", 60),
            max_pending_jobs=_int("MAX_PENDING_JOBS", 8),
            workers=_int("EXPORT_WORKERS", 2),
            analytics_script=os.getenv("ANALYTICS_SCRIPT", "").strip() or None,
            analytics_site_id=os.getenv("ANALYTICS_SITE_ID", "").strip() or None,
        )
