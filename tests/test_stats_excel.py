"""Statistiques, classeur Excel et CSV."""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from typing import TYPE_CHECKING

from conftest import PARIS, make_track
from openpyxl import load_workbook

from deezer_export.csv_export import csv_bytes, csv_text, save_csv
from deezer_export.excel import build_workbook, save_workbook, workbook_bytes
from deezer_export.stats import NO_GENRE, compute_stats, describe_track, format_duration
from deezer_export.table import PLAYLIST_HEADER

if TYPE_CHECKING:
    from pathlib import Path

    from deezer_export.models import PlaylistExport


def test_compute_stats(export: PlaylistExport) -> None:
    stats = compute_stats(export)
    assert (stats.track_count, stats.artist_count, stats.album_count) == (4, 2, 3)
    assert stats.total_duration == 879
    assert stats.average_duration == 219
    assert stats.longest is not None
    assert stats.longest.duration == 429
    assert stats.shortest is not None
    assert stats.shortest.duration == 100
    assert [(r.name, r.count) for r in stats.top_artists] == [
        ("The Beatles", 2),
        ("Aretha Franklin", 2),
    ]
    genres = {r.name: r.count for r in stats.top_genres}
    assert genres == {"Rock": 2, "Pop": 2, "R&B": 1, NO_GENRE: 1}
    assert stats.genre_count == 4
    assert abs(sum(r.share for r in stats.top_artists) - 1) < 1e-9


def test_homonymous_albums_are_kept_apart(export: PlaylistExport) -> None:
    tracks = (
        replace(make_track(1, album_id=1), album="Greatest Hits"),
        replace(make_track(2, album_id=1), album="Greatest Hits"),
        replace(make_track(3, album_id=2), album="Greatest Hits"),
    )
    stats = compute_stats(replace(export, tracks=tracks))
    assert stats.album_count == 2
    assert [(r.name, r.count) for r in stats.top_albums] == [
        ("Greatest Hits", 2),
        ("Greatest Hits (2)", 1),
    ]


def test_empty_playlist(export: PlaylistExport) -> None:
    stats = compute_stats(replace(export, tracks=()))
    assert stats.track_count == 0
    assert stats.average_duration == 0
    assert stats.longest is None
    assert stats.top_genres == ()


def test_formatting() -> None:
    assert format_duration(185) == "3 min 05 s"
    assert format_duration(8994) == "2 h 29 min 54 s"
    assert describe_track(None) == "—"
    assert (
        describe_track(make_track(1, artist="X", duration=61))
        == "Titre 1 — X (1 min 01 s)"
    )


def test_workbook_content(export: PlaylistExport) -> None:
    wb = build_workbook(export)
    assert wb.sheetnames == ["Playlist", "Statistiques"]

    playlist = wb["Playlist"]
    rows = list(playlist.iter_rows(values_only=True))
    assert rows[0] == (
        "title",
        "artist",
        "album",
        "duration_s",
        "link",
        "added_at",
        "rank",
        "explicit",
        "isrc",
        "genre 1",
        "genre 2",
        "genre 3",
        "genre 4",
    )
    assert rows[1][:4] == ("Titre 1", "The Beatles", "Album 10", 429)
    assert rows[1][5:9] == (None, None, None, None)  # champs inconnus : cellules vides
    assert rows[1][9:11] == ("Rock", "Pop")
    assert len(rows) == 5
    assert playlist.freeze_panes == "A2"
    assert "Playlist" in playlist.tables

    stats = wb["Statistiques"]
    overview = {
        r[0]: r[1]
        for r in stats.iter_rows(min_row=4, max_row=15, max_col=2, values_only=True)
    }
    assert overview["Playlist"] == "En mode 60"
    assert overview["Pistes"] == 4
    assert overview["Piste la plus populaire"] == "—"  # aucun rang connu
    assert overview["Export généré le"] == "13/11/2026 10:30"
    assert set(stats.tables) == {
        "Vue_ensemble",
        "Top_artistes",
        "Top_albums",
        "Top_genres",
    }  # pas de date d'ajout : pas de tableau par mois
    # openpyxl n'expose les graphiques que par un attribut privé.
    assert len(stats._charts) == 1  # pyright: ignore[reportAttributeAccessIssue]


def test_empty_playlist_workbook(export: PlaylistExport) -> None:
    wb = load_workbook(BytesIO(workbook_bytes(replace(export, tracks=(), genres={}))))
    assert wb["Playlist"].max_row == 1
    assert not wb["Playlist"].tables


def test_save_creates_parent_folder(tmp_path: Path, export: PlaylistExport) -> None:
    path = save_workbook(export, tmp_path / "a" / "b.xlsx")
    assert path.exists()
    assert load_workbook(path).sheetnames == ["Playlist", "Statistiques"]


def with_details(export: PlaylistExport) -> PlaylistExport:
    """La playlist de test, avec date d'ajout, rang, contenu explicite et ISRC."""
    added = (
        datetime(2026, 1, 5, 20, 0, tzinfo=PARIS),
        datetime(2026, 1, 31, 23, 30, tzinfo=PARIS),
        datetime(2026, 3, 1, 8, 0, tzinfo=PARIS),
        None,
    )
    tracks = tuple(
        replace(t, added_at=a, rank=r, explicit=e, isrc=i)
        for t, a, r, e, i in zip(
            export.tracks,
            added,
            (500_000, 900_000, None, 100_000),
            (False, True, None, False),
            ("GB0000000001", None, None, "US0000000004"),
            strict=True,
        )
    )
    return replace(export, tracks=tracks)


def test_stats_on_added_dates_and_popularity(export: PlaylistExport) -> None:
    stats = compute_stats(with_details(export))
    assert stats.added_by_month == (("2026-01", 2), ("2026-03", 1))
    assert stats.most_popular is not None
    assert stats.most_popular.title == "Titre 2"
    assert stats.least_popular is not None
    assert stats.least_popular.title == "Titre 4"


def test_workbook_details_and_months(export: PlaylistExport) -> None:
    wb = load_workbook(BytesIO(workbook_bytes(with_details(export))))
    playlist = wb["Playlist"]
    first = [c.value for c in playlist[2]]
    # Heure de Paris, sans fuseau : Excel n'en gère pas.
    assert first[5] == datetime(2026, 1, 5, 20, 0, tzinfo=PARIS).replace(tzinfo=None)
    assert playlist.cell(row=2, column=6).number_format == "dd/mm/yyyy hh:mm"
    assert first[6:9] == [500_000, False, "GB0000000001"]
    assert [c.value for c in playlist[5]][5:9] == [None, 100_000, False, "US0000000004"]

    stats = wb["Statistiques"]
    assert "Ajouts_par_mois" in stats.tables
    months = stats.tables["Ajouts_par_mois"].ref
    assert months == "M18:N20"
    cells = list(stats.iter_rows(min_row=18, max_row=20, min_col=13, values_only=True))
    assert cells == [("mois", "pistes"), ("2026-01", 2), ("2026-03", 1)]
    overview = {
        r[0]: str(r[1])
        for r in stats.iter_rows(min_row=4, max_row=15, max_col=2, values_only=True)
    }
    assert overview["Piste la plus populaire"].startswith("Titre 2 — The Beatles")
    assert overview["Piste la moins populaire"].startswith("Titre 4 — Aretha")


def test_csv(export: PlaylistExport, tmp_path: Path) -> None:
    data = csv_bytes(with_details(export))
    assert data.startswith(codecs.BOM_UTF8)  # Excel reconnaît l'UTF-8
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == list(PLAYLIST_HEADER)
    assert rows[1] == [
        "Titre 1",
        "The Beatles",
        "Album 10",
        "429",
        "https://www.deezer.com/track/1",
        "2026-01-05 20:00:00",
        "500000",
        "0",
        "GB0000000001",
        "Rock",
        "Pop",
        "",
        "",
    ]
    assert rows[4][5:9] == ["", "100000", "0", "US0000000004"]
    assert rows[2][7] == "1"
    assert len(rows) == 5

    path = save_csv(export, tmp_path / "sous" / "p.csv")
    assert path.read_bytes() == csv_bytes(export)
    assert csv_text(replace(export, tracks=())).strip() == ";".join(PLAYLIST_HEADER)
