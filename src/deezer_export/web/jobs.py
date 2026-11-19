"""Exports en tâche de fond : une grande playlist peut demander plusieurs minutes.

Deezer limite l'API publique à environ 10 requêtes par seconde et il faut une
requête par album pour les genres : le navigateur suit l'avancement plutôt que
d'attendre une seule réponse HTTP.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from deezer_export.deezer.errors import DeezerError

if TYPE_CHECKING:
    from collections.abc import Callable

    from deezer_export.models import PlaylistExport

log = logging.getLogger(__name__)

Status = Literal["queued", "running", "done", "error"]
STEP_LABELS = {
    "queued": "en file d'attente",
    "playlist": "lecture de la playlist",
    "tracks": "lecture des pistes",
    "albums": "genres des albums",
}
# Ce que l'on compte à chaque étape (« 300 / 2 140 pistes »).
STEP_UNITS = {"tracks": "pistes", "albums": "albums"}
# Part de chaque étape dans le pourcentage global. Les pistes se lisent par pages
# (100 ou 1 000 par requête) ; les genres demandent une requête par album, soit
# l'essentiel du temps.
STEP_SPANS = {"playlist": (0, 2), "tracks": (2, 20), "albums": (20, 100)}
_THIN_SPACE = "\u202f"  # séparateur de milliers : espace fine insécable


class TooManyJobsError(RuntimeError):
    """Trop d'exports en attente : réessayer un peu plus tard."""


@dataclass(slots=True)
class Job:
    """Un export, de la file d'attente au résultat."""

    id: str
    owner: str = field(repr=False)  # identifiant stable du propriétaire
    playlist_id: int
    status: Status = "queued"
    step: str = "queued"
    done: int = 0
    total: int = 0
    result: PlaylistExport | None = None
    error: str | None = None
    finished_at: float | None = None

    @property
    def step_label(self) -> str:
        """L'étape en cours, en français.

        Returns:
            str: Le libellé de l'étape.
        """
        return STEP_LABELS.get(self.step, self.step)

    @property
    def percent(self) -> int:
        """L'avancement estimé, en pour cent.

        Returns:
            int: Un entier entre 0 et 100.
        """
        if self.status == "done":
            return 100
        start, end = STEP_SPANS.get(self.step, (0, 0))
        if not self.total:
            return start
        return start + (end - start) * min(self.done, self.total) // self.total

    @property
    def count_label(self) -> str:
        """Le compteur de l'étape en cours, par exemple « 300 / 2 140 pistes ».

        Returns:
            str: Le compteur, ou une chaîne vide si l'étape ne compte rien.
        """
        unit = STEP_UNITS.get(self.step)
        if unit is None or not self.total:
            return ""
        return f"{_thousands(self.done)} / {_thousands(self.total)} {unit}"


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", _THIN_SPACE)


class JobManager:
    """File d'exports exécutés par un petit groupe de threads."""

    def __init__(
        self,
        workers: int = 2,
        max_pending: int = 8,
        ttl_seconds: float = 30 * 60,
    ) -> None:
        """Initialise la file.

        Args:
            workers (int): Nombre d'exports simultanés.
            max_pending (int): Nombre maximal d'exports en attente ou en cours.
            ttl_seconds (float): Durée de conservation d'un résultat.
        """
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="export"
        )
        self._max_pending = max_pending
        self._ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        owner: str,
        playlist_id: int,
        work: Callable[[Callable[[str, int, int], None]], PlaylistExport],
    ) -> Job:
        """Ajoute un export à la file.

        Un même propriétaire n'a qu'un export actif : en relancer un renvoie
        celui en cours s'il porte sur la même playlist.

        Args:
            owner (str): Identifiant stable du propriétaire.
            playlist_id (int): L'ID de la playlist.
            work (Callable[[Callable[[str, int, int], None]], PlaylistExport]):
                La fonction d'export, qui reçoit un rappel d'avancement.

        Returns:
            Job: L'export créé (ou celui déjà en cours).

        Raises:
            TooManyJobsError: File pleine, ou un autre export de ce propriétaire
                est déjà en cours.
        """
        with self._lock:
            self._purge()
            active = [
                j for j in self._jobs.values() if j.status in {"queued", "running"}
            ]
            mine = [j for j in active if j.owner == owner]
            if mine:
                if mine[0].playlist_id == playlist_id:
                    return mine[0]
                msg = "Un export est déjà en cours : attends qu'il se termine."
                raise TooManyJobsError(msg)
            if len(active) >= self._max_pending:
                msg = "Le service est très demandé : réessaie dans une minute."
                raise TooManyJobsError(msg)
            job = Job(
                id=secrets.token_urlsafe(16), owner=owner, playlist_id=playlist_id
            )
            self._jobs[job.id] = job

        self._executor.submit(self._run, job, work)
        return job

    def get(self, job_id: str, owner: str) -> Job | None:
        """Renvoie un export, seulement à son propriétaire.

        Args:
            job_id (str): L'identifiant de l'export.
            owner (str): Le propriétaire supposé.

        Returns:
            Job | None: L'export, ou None s'il n'existe pas ou appartient à un autre.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not secrets.compare_digest(job.owner, owner):
                return None
            return job

    def shutdown(self) -> None:
        """Arrête les threads (sans attendre les exports en cours)."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _run(
        job: Job,
        work: Callable[[Callable[[str, int, int], None]], PlaylistExport],
    ) -> None:
        def progress(step: str, done: int, total: int) -> None:
            job.step, job.done, job.total = step, done, total

        job.status = "running"
        try:
            job.result = work(progress)
            job.status = "done"
        except DeezerError as exc:
            job.error = str(exc)
            job.status = "error"
        except Exception:
            log.exception("export %s : erreur inattendue", job.id)
            job.error = "Erreur inattendue pendant l'export. Réessaie plus tard."
            job.status = "error"
        finally:
            job.finished_at = time.monotonic()

    def _purge(self) -> None:
        now = time.monotonic()
        stale = [
            k
            for k, j in self._jobs.items()
            if j.finished_at is not None and now - j.finished_at > self._ttl
        ]
        for key in stale:
            del self._jobs[key]
