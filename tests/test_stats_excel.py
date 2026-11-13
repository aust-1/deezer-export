"""Statistiques et classeur Excel."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from typing import TYPE_CHECKING

from conftest import make_track
from openpyxl import load_workbook

from deezer_export.excel import build_workbook, save_workbook, workbook_bytes
from deezer_export.stats import NO_GENRE, compute_stats, describe_track, format_duration

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
        "genre 1",
        "genre 2",
        "genre 3",
        "genre 4",
    )
    assert rows[1][:4] == ("Titre 1", "The Beatles", "Album 10", 429)
    assert rows[1][5:7] == ("Rock", "Pop")
    assert len(rows) == 5
    assert playlist.freeze_panes == "A2"
    assert "Playlist" in playlist.tables

    stats = wb["Statistiques"]
    overview = {
        r[0]: r[1]
        for r in stats.iter_rows(min_row=4, max_row=13, max_col=2, values_only=True)
    }
    assert overview["Playlist"] == "En mode 60"
    assert overview["Pistes"] == 4
    assert overview["Export généré le"] == "13/11/2026 10:30"
    assert set(stats.tables) == {
        "Vue_ensemble",
        "Top_artistes",
        "Top_albums",
        "Top_genres",
    }
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
