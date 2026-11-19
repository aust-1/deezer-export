"""Liste des pistes de la page de résultats : filtre, tri, pagination."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from conftest import PARIS, PLAYLIST, make_track

from deezer_export.models import PlaylistExport
from deezer_export.web.tracklist import PAGE_SIZE, track_page


@pytest.fixture
def big() -> PlaylistExport:
    """250 pistes : durées 1 à 250 s dans le désordre, deux albums."""
    tracks = [
        make_track(n, album_id=1 + n % 2, duration=(n * 37) % 250 + 1)
        for n in range(1, 251)
    ]
    tracks[9] = replace(tracks[9], title="Électro Café", artist="Daft Punk")
    return PlaylistExport(
        playlist=PLAYLIST,
        tracks=tuple(tracks),
        genres={1: ("Rock",), 2: ("Pop", "Dance")},
        generated_at=datetime(2026, 11, 13, tzinfo=PARIS),
    )


def test_pages_of_100(big: PlaylistExport) -> None:
    first = track_page(big)
    assert (first.page, first.pages, first.matched, first.total) == (1, 3, 250, 250)
    assert len(first.rows) == PAGE_SIZE
    assert (first.first, first.last) == (1, 100)
    last = track_page(big, page="3")
    assert [r.number for r in last.rows] == list(range(201, 251))
    assert (last.first, last.last) == (201, 250)


@pytest.mark.parametrize(
    ("asked", "page"), [("0", 1), ("-2", 1), ("x", 1), ("99", 3), ("9" * 30, 1), (2, 2)]
)
def test_out_of_range_pages_are_clamped(
    big: PlaylistExport, asked: str | int, page: int
) -> None:
    assert track_page(big, page=asked).page == page


def test_filter_matches_title_artist_album_and_genre_ignoring_case_and_accents(
    big: PlaylistExport,
) -> None:
    assert [r.number for r in track_page(big, query="electro cafe").rows] == [10]
    assert [r.number for r in track_page(big, query="  DAFT   punk ").rows] == [10]
    dance = track_page(big, query="dance")  # genre de l'album 2 (pistes impaires)
    assert dance.matched == 125
    assert all(r.number % 2 == 1 for r in dance.rows)
    assert track_page(big, query="rock").matched == 125  # genre de l'album 1
    assert [r.number for r in track_page(big, query="titre 25").rows] == [
        25,
        125,
        225,
        250,
    ]
    empty = track_page(big, query="introuvable")
    assert (empty.rows, empty.pages, empty.first, empty.last) == ((), 1, 0, 0)


def test_filter_is_trimmed(big: PlaylistExport) -> None:
    assert track_page(big, query="a" * 500).query == "a" * 100


def test_sort_by_number_and_duration(big: PlaylistExport) -> None:
    assert track_page(big, sort="-num").rows[0].number == 250
    by_duration = track_page(big, sort="duree")
    assert [r.track.duration for r in by_duration.rows[:3]] == [1, 2, 3]
    longest = track_page(big, sort="-duree")
    assert longest.rows[0].track.duration == 250
    assert track_page(big, sort="n'importe quoi").sort == "num"


def test_urls_keep_filter_and_sort(big: PlaylistExport) -> None:
    tp = track_page(big, query="rock & roll", sort="-duree", page="2")
    assert tp.url(page=3) == "?q=rock+%26+roll&tri=-duree&page=3#pistes"
    assert tp.url(sort="num") == "?q=rock+%26+roll#pistes"  # nouveau tri : page 1
    assert track_page(big).url() == "?#pistes"


def test_sort_links_toggle_direction(big: PlaylistExport) -> None:
    default = track_page(big)
    assert default.aria_sort("num") == "ascending"
    assert default.aria_sort("duree") == "none"
    assert default.sort_url("num") == "?tri=-num#pistes"
    assert default.sort_url("duree") == "?tri=duree#pistes"
    by_duration = track_page(big, sort="duree")
    assert by_duration.sort_url("duree") == "?tri=-duree#pistes"
    reverse = track_page(big, sort="-duree")
    assert reverse.aria_sort("duree") == "descending"
    assert reverse.sort_url("duree") == "?tri=duree#pistes"
