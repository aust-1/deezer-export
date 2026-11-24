"""Sessions côté serveur, en mémoire.

Le navigateur ne reçoit qu'un identifiant aléatoire (cookie HttpOnly). Le jeton
OAuth ou le cookie « arl » restent dans la mémoire du processus, jamais sur disque
ni dans les journaux, et disparaissent à la déconnexion, à expiration de la session
ou au redémarrage du service.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from deezer_export.models import PlaylistInfo, User

COOKIE_NAME = "dx_session"


@dataclass(slots=True)
class DeezerAuth:
    """Identité Deezer d'une session."""

    kind: Literal["oauth", "arl"]
    secret: str = field(repr=False)  # jeton OAuth ou cookie arl
    user: User
    expires_at: float | None = None  # horloge monotone ; None = selon la session

    def expired(self, now: float) -> bool:
        """Indique si l'authentification a expiré.

        Args:
            now (float): L'instant présent (horloge monotone).

        Returns:
            bool: Vrai si elle n'est plus utilisable.
        """
        return self.expires_at is not None and now >= self.expires_at


@dataclass(slots=True)
class Session:
    """Une session de navigation."""

    id: str = field(repr=False)
    csrf: str = field(repr=False)
    owner: str = field(repr=False)  # stable : ne change pas à la rotation de l'id
    last_seen: float = 0.0
    auth: DeezerAuth | None = None
    oauth_state: str | None = field(default=None, repr=False)
    playlists: tuple[float, list[PlaylistInfo]] | None = None  # cache (instant, liste)
    flash: str | None = None  # message affiché une fois, après une redirection
    is_new: bool = False


class SessionStore:
    """Sessions en mémoire, expirées après une période d'inactivité."""

    def __init__(
        self,
        ttl_seconds: float,
        max_sessions: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise un magasin vide.

        Args:
            ttl_seconds (float): Durée d'inactivité avant expiration.
            max_sessions (int): Nombre maximal de sessions (les plus anciennes sautent).
            clock (Callable[[], float]): Horloge monotone (injectable pour les tests).
        """
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def load(self, session_id: str | None) -> Session:
        """Renvoie la session de cet identifiant, ou une nouvelle session.

        Args:
            session_id (str | None): L'identifiant lu dans le cookie.

        Returns:
            Session: La session (``is_new`` si elle vient d'être créée).
        """
        now = self._clock()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(session_id or "")
            if session is not None:
                session.last_seen = now
                session.is_new = False
                if session.auth and session.auth.expired(now):
                    session.auth = None
                    session.playlists = None
                return session
            return self._create(now)

    def rotate(self, session: Session) -> Session:
        """Change l'identifiant d'une session (à chaque connexion ou déconnexion).

        Args:
            session (Session): La session à renouveler.

        Returns:
            Session: La même session, avec un nouvel identifiant et un nouveau
                jeton CSRF.
        """
        with self._lock:
            self._sessions.pop(session.id, None)
            session.id = secrets.token_urlsafe(32)
            session.csrf = secrets.token_urlsafe(32)
            session.is_new = True
            self._sessions[session.id] = session
            return session

    def __len__(self) -> int:
        """Nombre de sessions actives.

        Returns:
            int: Le nombre de sessions.
        """
        with self._lock:
            return len(self._sessions)

    def _create(self, now: float) -> Session:
        if len(self._sessions) >= self._max:
            oldest = min(self._sessions.values(), key=lambda s: s.last_seen)
            del self._sessions[oldest.id]
        session = Session(
            id=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            owner=secrets.token_urlsafe(32),
            last_seen=now,
            is_new=True,
        )
        self._sessions[session.id] = session
        return session

    def _purge(self, now: float) -> None:
        expired = [
            k for k, s in self._sessions.items() if now - s.last_seen > self._ttl
        ]
        for key in expired:
            del self._sessions[key]
