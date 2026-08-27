"""Exportation de playlists Deezer vers Excel."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime as dt
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.data_source import AxDataSource, StrRef
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

ACCENT_COLOR = "4472C4"  # bleu du style de tableau TableStyleMedium2
API = "https://api.deezer.com"
ENV_PLAYLIST_ID = "DEEZER_PLAYLIST_ID"
GENRE_COLUMNS = 4
TOP_N = 10
PARIS = ZoneInfo("Europe/Paris")


def _get_json(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _fetch_tracks(playlist_id: str) -> list[dict]:
    tracks: list[dict] = []
    url: str | None = f"{API}/playlist/{playlist_id}/tracks"
    params: dict | None = {"limit": 100}

    while url:
        data = _get_json(url, params)
        tracks.extend(data["data"])
        url = data.get("next")
        params = None

    return tracks


def _build_genre_cache(album_ids: set[int]) -> dict[int, list[str]]:
    cache: dict[int, list[str]] = {}

    for album_id in album_ids:
        album = _get_json(f"{API}/album/{album_id}")
        cache[album_id] = [g["name"] for g in album.get("genres", {}).get("data", [])]

    return cache


def export_playlist(playlist_id: str, output: str = "playlist.xlsx") -> None:
    """Exporte une playlist Deezer vers un fichier Excel.

    Le classeur contient deux feuilles : "Playlist" (une ligne par piste) et
    "Statistiques" (indicateurs globaux et classements).

    Args:
        playlist_id (str): L'ID de la playlist à exporter.
        output (str, optional):
            Le chemin du fichier de sortie. Defaults to "playlist.xlsx".
    """
    tracks = _fetch_tracks(playlist_id)
    album_ids = {t["album"]["id"] for t in tracks}
    genre_cache = _build_genre_cache(album_ids)

    wb = Workbook()
    wb.remove(wb.worksheets[0])  # feuille vide créée par défaut

    _build_playlist_sheet(wb.create_sheet("Playlist"), tracks, genre_cache)
    _build_stats_sheet(wb.create_sheet("Statistiques"), tracks, genre_cache)

    wb.save(output)
    print(
        f"{len(tracks)} pistes exportées vers {output} "
        f"({len(album_ids)} albums interrogés)"
    )


def _build_playlist_sheet(
    ws: Worksheet,
    tracks: list[dict],
    genre_cache: dict[int, list[str]],
) -> None:
    header = ["title", "artist", "album", "duration_s", "link"]
    header += [f"genre {i}" for i in range(1, GENRE_COLUMNS + 1)]
    ws.append(header)

    for t in tracks:
        genres = genre_cache.get(t["album"]["id"], [])
        genres = (genres + [""] * GENRE_COLUMNS)[:GENRE_COLUMNS]
        ws.append([
            t["title"],
            t["artist"]["name"],
            t["album"]["title"],
            t["duration"],
            t["link"],
            *genres,
        ])

    _style_header(ws, row=1, col=1, n_cols=len(header))
    ws.freeze_panes = "A2"

    last_col = get_column_letter(len(header))
    _add_table(ws, "Playlist", f"A1:{last_col}{len(tracks) + 1}")
    _autofit(ws, n_cols=len(header))


def _build_stats_sheet(
    ws: Worksheet,
    tracks: list[dict],
    genre_cache: dict[int, list[str]],
) -> None:
    """Construit la feuille "Statistiques" du classeur.

    Args:
        ws (Worksheet): La feuille à remplir.
        tracks (list[dict]): Les pistes de la playlist.
        genre_cache (dict[int, list[str]]): Les genres indexés par ID d'album.
    """
    artists = Counter(t["artist"]["name"] for t in tracks)
    albums = Counter(t["album"]["title"] for t in tracks)
    genres: Counter[str] = Counter()
    for t in tracks:
        genres.update(set(genre_cache.get(t["album"]["id"], [])) or {"(sans genre)"})

    overview = _overview_rows(tracks, artists, albums, genres)
    _write_overview(ws, overview)

    row = len(overview) + 5
    artists_last = _write_ranking(
        ws, 1, row, "Top artistes", "Top_artistes", "artiste", artists
    )
    albums_last = _write_ranking(
        ws, 5, row, "Top albums", "Top_albums", "album", albums
    )
    genres_last = _write_ranking(
        ws, 9, row, "Top genres", "Top_genres", "genre", genres
    )

    last = max(artists_last, albums_last, genres_last)
    _add_genre_chart(ws, row=row + 1, last_row=genres_last, anchor=f"A{last + 3}")
    _autofit(ws, n_cols=11)


def _overview_rows(
    tracks: list[dict],
    artists: Counter[str],
    albums: Counter[str],
    genres: Counter[str],
) -> list[tuple[str, str | int]]:
    total_s = sum(int(t["duration"]) for t in tracks)
    longest = max(tracks, key=lambda t: int(t["duration"]), default=None)
    shortest = min(tracks, key=lambda t: int(t["duration"]), default=None)

    return [
        ("Pistes", len(tracks)),
        ("Artistes uniques", len(artists)),
        ("Albums uniques", len(albums)),
        ("Genres uniques", len(genres)),
        ("Durée totale", _format_duration(total_s)),
        ("Durée moyenne", _format_duration(total_s // len(tracks) if tracks else 0)),
        ("Piste la plus longue", _describe_track(longest)),
        ("Piste la plus courte", _describe_track(shortest)),
        ("Export généré le", dt.now(PARIS).strftime("%d/%m/%Y %H:%M")),
    ]


def _write_overview(ws: Worksheet, rows: list[tuple[str, str | int]]) -> None:
    ws.cell(row=1, column=1, value="Vue d'ensemble").font = Font(bold=True, size=14)
    ws.cell(row=3, column=1, value="indicateur")
    ws.cell(row=3, column=2, value="valeur")

    for i, (label, value) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=label)
        ws.cell(row=i, column=2, value=value)

    _style_header(ws, row=3, col=1, n_cols=2)
    _add_table(ws, "Vue_ensemble", f"A3:B{len(rows) + 3}")


def _write_ranking(
    ws: Worksheet,
    col: int,
    row: int,
    title: str,
    name: str,
    label: str,
    counts: Counter[str],
) -> int:
    """Écrit un classement (nom, nombre de pistes, part) sous forme de tableau.

    Args:
        ws (Worksheet): La feuille à remplir.
        col (int): L'index (1-based) de la première colonne du classement.
        row (int): L'index (1-based) de la ligne du titre.
        title (str): Le titre affiché au-dessus du tableau.
        name (str): Le nom interne du tableau Excel (sans espace).
        label (str): L'en-tête de la première colonne.
        counts (Counter[str]): Les occurrences à classer.

    Returns:
        int: L'index de la dernière ligne écrite.
    """
    total = sum(counts.values())
    top = counts.most_common(TOP_N)

    ws.cell(row=row, column=col, value=title).font = Font(bold=True, size=12)
    for offset, head in enumerate((label, "pistes", "part")):
        ws.cell(row=row + 1, column=col + offset, value=head)

    for i, (key, count) in enumerate(top, start=1):
        ws.cell(row=row + 1 + i, column=col, value=key)
        ws.cell(row=row + 1 + i, column=col + 1, value=count)
        cell = ws.cell(row=row + 1 + i, column=col + 2, value=count / total)
        cell.number_format = "0.0%"

    last_row = row + 1 + len(top)
    if top:
        _style_header(ws, row=row + 1, col=col, n_cols=3)
        first, last = get_column_letter(col), get_column_letter(col + 2)
        _add_table(ws, name, f"{first}{row + 1}:{last}{last_row}")

    return last_row


def _add_genre_chart(ws: Worksheet, row: int, last_row: int, anchor: str) -> None:
    if last_row <= row:  # pas de genre à représenter
        return

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Genres les plus représentés"
    chart.title.overlay = False  # sinon le titre se superpose aux barres
    chart.legend = None
    chart.varyColors = False
    chart.height = 8
    chart.width = 16
    chart.add_data(
        Reference(ws, min_col=10, min_row=row, max_row=last_row),
        titles_from_data=True,
    )
    # set_categories() écrirait une référence numérique : Excel n'afficherait
    # alors aucun nom de genre sur l'axe des catégories.
    cats = Reference(ws, min_col=9, min_row=row + 1, max_row=last_row)
    chart.series[0].cat = AxDataSource(strRef=StrRef(f=str(cats)))
    chart.series[0].graphicalProperties.solidFill = ACCENT_COLOR

    # openpyxl masque les axes par défaut : sans cela, Excel n'affiche ni le nom
    # des genres (axe des catégories) ni l'échelle (axe des valeurs).
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.x_axis.title = None
    chart.y_axis.title = None
    chart.x_axis.majorTickMark = "out"
    chart.y_axis.majorGridlines = None

    # Le genre le plus représenté en haut, avec les valeurs affichées.
    chart.x_axis.scaling.orientation = "maxMin"
    chart.y_axis.crosses = "max"
    chart.dLbls = DataLabelList(
        showVal=True,
        showSerName=False,
        showCatName=False,
        showLegendKey=False,
        showPercent=False,
        showBubbleSize=False,
    )

    ws.add_chart(chart, anchor)


def _describe_track(track: dict | None) -> str:
    if track is None:
        return "—"
    return (
        f"{track['title']} — {track['artist']['name']} "
        f"({_format_duration(int(track['duration']))})"
    )


def _format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {secs:02d} s"
    return f"{minutes} min {secs:02d} s"


def _style_header(ws: Worksheet, row: int, col: int, n_cols: int) -> None:
    for offset in range(n_cols):
        ws.cell(row=row, column=col + offset).font = Font(bold=True, color="FFFFFF")


def _add_table(ws: Worksheet, name: str, ref: str) -> None:
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def _autofit(ws: Worksheet, n_cols: int) -> None:
    for col_idx in range(1, n_cols + 1):
        letter = get_column_letter(col_idx)
        width = max(
            (len(str(cell.value)) for cell in ws[letter] if cell.value is not None),
            default=0,
        )
        ws.column_dimensions[letter].width = min(width + 2, 60)


def _playlist_id_from_env() -> str:
    """Lit l'ID de la playlist dans le fichier `.env` ou l'environnement.

    Returns:
        str: L'ID de la playlist à exporter.

    Raises:
        SystemExit: Si la variable `DEEZER_PLAYLIST_ID` n'est pas renseignée.
    """
    load_dotenv()
    playlist_id = os.getenv(ENV_PLAYLIST_ID, "").strip()

    if not playlist_id:
        msg = (
            f"{ENV_PLAYLIST_ID} n'est pas défini : copiez .env.example vers .env "
            "et renseignez l'ID de la playlist à exporter."
        )
        raise SystemExit(msg)

    return playlist_id


if __name__ == "__main__":
    print("Exportation de la playlist Deezer vers un fichier Excel...")
    export_playlist(
        _playlist_id_from_env(),
        f"output/playlist_{dt.now(PARIS).date().isoformat()}.xlsx",
    )
