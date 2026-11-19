"""Ligne de commande : ``deezer-export [PLAYLIST] [-o FICHIER] [-f FMT] [--mine]``.

Le format (``xlsx`` ou ``csv``) suit l'extension du fichier, sauf avec ``--format``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime as dt
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from deezer_export.deezer.errors import DeezerError
from deezer_export.service import (
    FORMATS,
    PARIS,
    InvalidPlaylistReferenceError,
    export_playlist,
    make_source,
    output_format,
    parse_playlist_reference,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

FORMAT_NAMES = {"xlsx": "Excel", "csv": "CSV"}
ENV_PLAYLIST_ID = "DEEZER_PLAYLIST_ID"
ENV_ARL = "DEEZER_ARL"
ENV_ACCESS_TOKEN = "DEEZER_ACCESS_TOKEN"  # ruff: ignore[hardcoded-password-string] - nom de variable, pas un secret


def build_parser() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments.

    Returns:
        argparse.ArgumentParser: L'analyseur.
    """
    parser = argparse.ArgumentParser(
        prog="deezer-export",
        description="Exporte une playlist Deezer vers un classeur Excel ou un CSV.",
        epilog=(
            f"Authentification (playlists privées) : {ENV_ARL} ou {ENV_ACCESS_TOKEN}, "
            "dans l'environnement ou le fichier .env."
        ),
    )
    parser.add_argument(
        "playlist",
        nargs="?",
        help=f"ID ou lien de la playlist (défaut : {ENV_PLAYLIST_ID})",
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "fichier à écrire, .xlsx ou .csv d'après l'extension "
            "(défaut : output/playlist_<id>_<date>.xlsx)"
        ),
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        help="format du fichier, quelle que soit son extension (défaut : xlsx)",
    )
    parser.add_argument(
        "--mine",
        action="store_true",
        help="liste les playlists du compte connecté, puis quitte",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de la ligne de commande.

    Args:
        argv (Sequence[str] | None): Les arguments (défaut : ceux du processus).

    Returns:
        int: Le code de sortie (0 si tout s'est bien passé).
    """
    args = build_parser().parse_args(argv)
    load_dotenv()
    arl = os.getenv(ENV_ARL, "").strip() or None
    token = os.getenv(ENV_ACCESS_TOKEN, "").strip() or None

    try:
        if args.mine:
            return _list_mine(arl=arl, access_token=token)
        return _export(
            args.playlist, args.output, args.format, arl=arl, access_token=token
        )
    except (DeezerError, InvalidPlaylistReferenceError) as exc:
        _err(str(exc))
        return 1


def _export(
    playlist_arg: str | None,
    output_arg: str | None,
    format_arg: str | None,
    *,
    arl: str | None,
    access_token: str | None,
) -> int:
    playlist = (playlist_arg or os.getenv(ENV_PLAYLIST_ID) or "").strip()
    if not playlist:
        _err(
            f"Indique une playlist (argument ou {ENV_PLAYLIST_ID} dans .env, "
            "voir .env.example)."
        )
        return 2

    playlist_id = parse_playlist_reference(playlist)
    output = output_arg or _default_output(playlist_id, format_arg or "xlsx")
    fmt = output_format(output, format_arg)
    _out(f"Exportation de la playlist Deezer vers un fichier {FORMAT_NAMES[fmt]}…")
    export = export_playlist(
        playlist_id, output, arl=arl, access_token=access_token, file_format=fmt
    )
    _out(
        f"{len(export.tracks)} pistes exportées vers {output} "
        f"({export.albums_queried} albums interrogés)"
    )
    return 0


def _list_mine(*, arl: str | None, access_token: str | None) -> int:
    if not arl and not access_token:
        _err(f"--mine nécessite {ENV_ARL} ou {ENV_ACCESS_TOKEN}.")
        return 2
    source, _ = make_source(arl=arl, access_token=access_token)
    for p in source.my_playlists():
        visibility = "publique" if p.public else "privée"
        _out(f"{p.id}\t{p.nb_tracks:>5} pistes\t{visibility}\t{p.title}")
    return 0


def _default_output(playlist_id: int, fmt: str) -> str:
    return f"output/playlist_{playlist_id}_{dt.now(PARIS).date().isoformat()}.{fmt}"


def _out(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _err(message: str) -> None:
    sys.stderr.write(f"Erreur : {message}\n")


if __name__ == "__main__":
    raise SystemExit(main())
