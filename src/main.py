import time
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import _WorksheetOrChartsheetLike
from openpyxl.worksheet.table import Table, TableStyleInfo

API = "https://api.deezer.com"
GENRE_COLUMNS = 4


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
        time.sleep(0.05)  # ménage le rate limit (~50 req/10 s)

    return cache


def export_playlist(playlist_id: str, output: str = "playlist.xlsx") -> None:
    tracks = _fetch_tracks(playlist_id)
    album_ids = {t["album"]["id"] for t in tracks}
    genre_cache = _build_genre_cache(album_ids)

    header = ["title", "artist", "album", "duration_s", "link"]
    header += [f"genre {i}" for i in range(1, GENRE_COLUMNS + 1)]

    wb = Workbook()
    if not wb.worksheets:
        wb.create_sheet()
    if not wb.active:
        wb.active = wb.worksheets[0]
    ws: _WorksheetOrChartsheetLike = wb.active
    ws.title = "Playlist"
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

    _format_sheet(ws, n_cols=len(header), n_rows=len(tracks))

    wb.save(output)
    print(
        f"{len(tracks)} pistes exportées vers {output} "
        f"({len(album_ids)} albums interrogés)"
    )


def _format_sheet(ws, n_cols: int, n_rows: int) -> None:
    last_col = get_column_letter(n_cols)
    last_row = n_rows + 1  # +1 pour la ligne d'en-tête

    # En-tête en gras + volet figé pour garder l'en-tête visible au défilement
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    # Vrai tableau Excel : filtres, lignes alternées, plage nommée
    table = Table(displayName="Playlist", ref=f"A1:{last_col}{last_row}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)

    # Largeur des colonnes ajustée au contenu
    for col_idx in range(1, n_cols + 1):
        letter = get_column_letter(col_idx)
        width = max(
            (len(str(cell.value)) for cell in ws[letter] if cell.value is not None),
            default=0,
        )
        ws.column_dimensions[letter].width = min(width + 2, 60)


if __name__ == "__main__":
    print("Exportation de la playlist Deezer vers un fichier Excel...")
    export_playlist(
        "10016300382",
        f"output/playlist_{dt.now(ZoneInfo('Europe/Paris')).date().isoformat()}.xlsx",
    )
