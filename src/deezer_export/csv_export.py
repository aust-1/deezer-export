"""Export CSV de la feuille « Playlist » : UTF-8, séparateur ``;`` (Excel en français).

Le fichier commence par une marque d'ordre des octets (BOM) : sans elle, Excel lit le
fichier en Windows-1252 et abîme les accents. Python la retire avec
``encoding="utf-8-sig"``.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from deezer_export.table import PLAYLIST_HEADER, playlist_rows

if TYPE_CHECKING:
    from deezer_export.models import PlaylistExport
    from deezer_export.table import Cell

DELIMITER = ";"
ENCODING = "utf-8-sig"
MEDIA_TYPE = "text/csv; charset=utf-8"


def csv_text(export: PlaylistExport) -> str:
    """Construit le CSV d'une playlist : un en-tête, puis une ligne par piste.

    Args:
        export (PlaylistExport): La playlist récupérée.

    Returns:
        str: Le contenu du fichier (sans BOM).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=DELIMITER, lineterminator="\r\n")
    writer.writerow(PLAYLIST_HEADER)
    writer.writerows([_cell(v) for v in row] for row in playlist_rows(export))
    return buffer.getvalue()


def csv_bytes(export: PlaylistExport) -> bytes:
    """Construit le fichier CSV d'une playlist en mémoire.

    Args:
        export (PlaylistExport): La playlist récupérée.

    Returns:
        bytes: Le contenu du fichier, en UTF-8 avec BOM.
    """
    return csv_text(export).encode(ENCODING)


def save_csv(export: PlaylistExport, output: str | Path) -> Path:
    """Enregistre le CSV d'une playlist, en créant le dossier au besoin.

    Args:
        export (PlaylistExport): La playlist récupérée.
        output (str | Path): Le chemin du fichier ``.csv``.

    Returns:
        Path: Le chemin du fichier écrit.
    """
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(csv_bytes(export))
    return path


def _cell(value: Cell) -> str | int:
    if value is None:
        return ""
    if isinstance(value, bool):  # avant int : bool en hérite
        return int(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value
