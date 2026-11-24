"""Construction du classeur Excel : feuilles « Playlist » et « Statistiques »."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.data_source import AxDataSource, StrRef
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from deezer_export.stats import compute_stats, describe_track, format_duration

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

    from deezer_export.models import PlaylistExport
    from deezer_export.stats import PlaylistStats, RankingRow

ACCENT_COLOR = "4472C4"  # bleu du style de tableau TableStyleMedium2
GENRE_COLUMNS = 4
MAX_COLUMN_WIDTH = 60


def build_workbook(export: PlaylistExport) -> Workbook:
    """Construit le classeur d'une playlist.

    Args:
        export (PlaylistExport): La playlist récupérée.

    Returns:
        Workbook: Le classeur, prêt à être enregistré.
    """
    stats = compute_stats(export)
    wb = Workbook()
    wb.remove(wb.worksheets[0])  # feuille vide créée par défaut

    _build_playlist_sheet(wb.create_sheet("Playlist"), export)
    _build_stats_sheet(wb.create_sheet("Statistiques"), export, stats)
    return wb


def save_workbook(export: PlaylistExport, output: str | Path) -> Path:
    """Enregistre le classeur d'une playlist, en créant le dossier au besoin.

    Args:
        export (PlaylistExport): La playlist récupérée.
        output (str | Path): Le chemin du fichier ``.xlsx``.

    Returns:
        Path: Le chemin du fichier écrit.
    """
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(export).save(path)
    return path


def workbook_bytes(export: PlaylistExport) -> bytes:
    """Construit le classeur d'une playlist en mémoire.

    Args:
        export (PlaylistExport): La playlist récupérée.

    Returns:
        bytes: Le contenu du fichier ``.xlsx``.
    """
    buffer = BytesIO()
    build_workbook(export).save(buffer)
    return buffer.getvalue()


def _build_playlist_sheet(ws: Worksheet, export: PlaylistExport) -> None:
    header = ["title", "artist", "album", "duration_s", "link"]
    header += [f"genre {i}" for i in range(1, GENRE_COLUMNS + 1)]
    ws.append(header)

    for t in export.tracks:
        genres = [*export.genres_of(t), *[""] * GENRE_COLUMNS][:GENRE_COLUMNS]
        ws.append([t.title, t.artist, t.album, t.duration, t.link, *genres])

    _style_header(ws, row=1, col=1, n_cols=len(header))
    ws.freeze_panes = "A2"

    if export.tracks:  # Excel refuse un tableau sans ligne de données
        last_col = get_column_letter(len(header))
        _add_table(ws, "Playlist", f"A1:{last_col}{len(export.tracks) + 1}")
    _autofit(ws, n_cols=len(header))


def _build_stats_sheet(
    ws: Worksheet,
    export: PlaylistExport,
    stats: PlaylistStats,
) -> None:
    overview: list[tuple[str, str | int]] = [
        ("Playlist", export.playlist.title),
        ("Pistes", stats.track_count),
        ("Artistes uniques", stats.artist_count),
        ("Albums uniques", stats.album_count),
        ("Genres uniques", stats.genre_count),
        ("Durée totale", format_duration(stats.total_duration)),
        ("Durée moyenne", format_duration(stats.average_duration)),
        ("Piste la plus longue", describe_track(stats.longest)),
        ("Piste la plus courte", describe_track(stats.shortest)),
        ("Export généré le", export.generated_at.strftime("%d/%m/%Y %H:%M")),
    ]
    _write_overview(ws, overview)

    row = len(overview) + 5
    rankings = (
        (1, "Top artistes", "Top_artistes", "artiste", stats.top_artists),
        (5, "Top albums", "Top_albums", "album", stats.top_albums),
        (9, "Top genres", "Top_genres", "genre", stats.top_genres),
    )
    last_rows = [
        _write_ranking(ws, col, row, (title, name, label), rows)
        for col, title, name, label, rows in rankings
    ]

    _add_genre_chart(
        ws, row=row + 1, last_row=last_rows[2], anchor=f"A{max(last_rows) + 3}"
    )
    _autofit(ws, n_cols=11)


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
    names: tuple[str, str, str],
    rows: tuple[RankingRow, ...],
) -> int:
    """Écrit un classement (nom, nombre de pistes, part) sous forme de tableau.

    Args:
        ws (Worksheet): La feuille à remplir.
        col (int): L'index (1-based) de la première colonne du classement.
        row (int): L'index (1-based) de la ligne du titre.
        names (tuple[str, str, str]): Le titre affiché, le nom interne du tableau
            Excel (sans espace) et l'en-tête de la première colonne.
        rows (tuple[RankingRow, ...]): Les lignes du classement.

    Returns:
        int: L'index de la dernière ligne écrite.
    """
    title, name, label = names
    ws.cell(row=row, column=col, value=title).font = Font(bold=True, size=12)
    for offset, head in enumerate((label, "pistes", "part")):
        ws.cell(row=row + 1, column=col + offset, value=head)

    for i, ranking in enumerate(rows, start=1):
        ws.cell(row=row + 1 + i, column=col, value=ranking.name)
        ws.cell(row=row + 1 + i, column=col + 1, value=ranking.count)
        cell = ws.cell(row=row + 1 + i, column=col + 2, value=ranking.share)
        cell.number_format = "0.0%"

    last_row = row + 1 + len(rows)
    if rows:
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
    # openpyxl convertit le texte en objet Title (annotation trop étroite).
    chart.title.overlay = False  # pyright: ignore[reportAttributeAccessIssue]
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
        ws.column_dimensions[letter].width = min(width + 2, MAX_COLUMN_WIDTH)
