"""Outils HTTP communs : session, limitation de débit."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import requests

from deezer_export._version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable

TIMEOUT = 15  # secondes, par requête
USER_AGENT = f"deezer-export/{__version__} (+https://github.com/aust-1/deezer-export)"


class RateLimiter:
    """Limiteur à fenêtre glissante, partagé entre threads.

    L'API publique de Deezer accepte environ 50 requêtes toutes les 5 secondes par
    adresse IP : le limiteur par défaut garde une marge (45 / 5 s).
    """

    def __init__(
        self,
        max_calls: int = 45,
        period: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise le limiteur.

        Args:
            max_calls (int): Nombre d'appels autorisés par fenêtre.
            period (float): Durée de la fenêtre, en secondes.
            clock (Callable[[], float]): Horloge monotone (injectable pour les tests).
            sleep (Callable[[float], None]): Fonction d'attente (idem).
        """
        self._max_calls = max_calls
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Attend, si besoin, qu'un appel soit autorisé, puis l'enregistre."""
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self._period:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                wait = self._period - (now - self._calls[0])
            self._sleep(max(wait, 0.01))


def make_session() -> requests.Session:
    """Crée une session HTTP identifiée par un User-Agent explicite.

    Returns:
        requests.Session: La session prête à l'emploi.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session
