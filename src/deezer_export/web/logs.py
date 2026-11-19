"""Journal d'accès sans chaîne de requête : aucun secret d'authentification n'y entre.

Au retour de Deezer, ``/connexion/deezer/retour?code=…&state=…`` porte le code
d'autorisation OAuth. Le journal d'accès d'uvicorn écrit par défaut le chemin complet :
ce filtre en retire la chaîne de requête, pour toutes les routes (aucune n'a besoin
d'être journalisée avec ses paramètres).
"""

from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"
# Arguments d'une ligne du journal d'accès d'uvicorn :
# (adresse du client, méthode, chemin + requête, version HTTP, statut).
_ACCESS_ARGS = 5
_PATH = 2


class StripQueryStringFilter(logging.Filter):
    """Retire la chaîne de requête du chemin des lignes du journal d'accès."""

    def filter(self, record: logging.LogRecord) -> bool:  # ruff: ignore[no-self-use] - interface de logging.Filter
        """Réécrit le chemin journalisé, sans sa chaîne de requête.

        Args:
            record (logging.LogRecord): La ligne du journal d'accès.

        Returns:
            bool: Toujours vrai : la ligne est gardée, seul son chemin change.
        """
        args = record.args
        if isinstance(args, tuple) and len(args) == _ACCESS_ARGS:
            path = args[_PATH]
            if isinstance(path, str) and "?" in path:
                record.args = (*args[:_PATH], strip_query(path), *args[_PATH + 1 :])
        return True


def strip_query(path: str) -> str:
    """Retire la chaîne de requête d'un chemin.

    Args:
        path (str): Le chemin, éventuellement suivi de ``?…``.

    Returns:
        str: Le chemin seul.
    """
    return path.split("?", 1)[0]


def install_access_log_filter(logger_name: str = ACCESS_LOGGER) -> None:
    """Pose le filtre sur le journal d'accès (une seule fois par processus).

    Le filtre est posé sur le logger et non sur un handler : il s'applique quelle que
    soit la configuration des journaux (``deezer-export-web``, ``uvicorn`` en direct…).

    Args:
        logger_name (str): Le logger du journal d'accès.
    """
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, StripQueryStringFilter) for f in logger.filters):
        logger.addFilter(StripQueryStringFilter())
