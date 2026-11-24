"""Export de playlists Deezer vers Excel, en ligne de commande ou sur le web."""

from deezer_export._version import __version__
from deezer_export.service import export_playlist

__all__ = ["__version__", "export_playlist"]
