"""Récupération d'une playlist, références de playlist, cache des genres."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest
import requests
import responses
from conftest import PARIS, PLAYLIST, make_track

from deezer_export.deezer.gateway import GatewayClient
from deezer_export.deezer.public import PublicClient
from deezer_export.service import (
    GenreCache,
    InvalidPlaylistReferenceError,
    export_playlist,
    fetch_playlist,
    make_source,
    parse_playlist_reference,
)

if TYPE_CHECKING:
    from pathlib import Path

    from deezer_export.models import PlaylistExport, PlaylistInfo, Track


@pytest.mark.parametrize(
    "value",
    [
        "908622995",
        "  908622995 ",
        "https://www.deezer.com/fr/playlist/908622995",
        "https://www.deezer.com/playlist/908622995?utm_source=x",
        "deezer.com/en/playlist/908622995",
    ],
)
def test_parse_playlist_reference(value: str) -> None:
    assert parse_playlist_reference(value) == 908622995


@pytest.mark.parametrize(
    "value",
    [
        "",
        "nimporte",
        "https://evil.example/playlist/1",
        "https://www.deezer.com/album/1",
    ],
)
def test_parse_playlist_reference_rejects(value: str) -> None:
    with pytest.raises(InvalidPlaylistReferenceError):
        parse_playlist_reference(value)


@responses.activate
def test_short_share_link_is_followed() -> None:
    responses.head(
        "https://link.deezer.com/s/abc",
        status=302,
        headers={"Location": "https://www.deezer.com/fr/playlist/42?host=1"},
    )
    responses.head("https://www.deezer.com/fr/playlist/42", status=200)
    assert parse_playlist_reference("https://link.deezer.com/s/abc") == 42


@responses.activate
def test_short_share_link_unreachable() -> None:
    responses.head(
        "https://link.deezer.com/s/abc", body=requests.ConnectionError("down")
    )
    with pytest.raises(InvalidPlaylistReferenceError, match="lien complet"):
        parse_playlist_reference("https://link.deezer.com/s/abc")


class FakeSource:
    def __init__(self, tracks: list[Track]) -> None:
        self.tracks = tracks

    def playlist(self, playlist_id: int) -> PlaylistInfo:
        assert playlist_id == PLAYLIST.id
        return PLAYLIST

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        return self.tracks

    def my_playlists(self) -> list[PlaylistInfo]:
        return [PLAYLIST]


class FakeGenres:
    def __init__(self) -> None:
        self.asked: list[int] = []

    def album_genres(self, album_id: int) -> tuple[str, ...]:
        self.asked.append(album_id)
        return (f"Genre {album_id}",)


def test_fetch_playlist_queries_each_album_once() -> None:
    tracks = [
        make_track(1, album_id=1),
        make_track(2, album_id=1),
        make_track(3, album_id=2),
        make_track(4, album_id=0),
    ]
    genres = FakeGenres()
    cache = GenreCache()
    cache.put(2, ("En cache",))
    steps: list[tuple[str, int, int]] = []
    now = datetime(2026, 1, 1, tzinfo=PARIS)

    export = fetch_playlist(
        PLAYLIST.id,
        FakeSource(tracks),
        genres,  # type: ignore[arg-type]
        cache=cache,
        progress=lambda s, d, t: steps.append((s, d, t)),
        now=lambda: now,
    )

    assert genres.asked == [1]  # album 2 en cache, album 0 ignoré
    assert export.genres == {1: ("Genre 1",), 2: ("En cache",)}
    assert export.albums_queried == 2
    assert export.generated_at == now
    assert steps[0] == ("playlist", 0, 0)
    assert steps[-1] == ("albums", 2, 2)
    assert cache.get(1) == ("Genre 1",)


def test_genre_cache_expiry_and_eviction() -> None:
    cache = GenreCache(ttl=-1)
    cache.put(1, ("Pop",))
    assert cache.get(1) is None
    small = GenreCache(max_size=1)
    small.put(1, ("Pop",))
    small.put(2, ("Rock",))
    assert small.get(1) is None
    assert small.get(2) == ("Rock",)


def test_make_source() -> None:
    source, public = make_source()
    assert isinstance(source, PublicClient)
    assert source is public
    assert not public.authenticated
    source, public = make_source(arl="a" * 64)
    assert isinstance(source, GatewayClient)
    source, public = make_source(access_token="tok")
    assert source is public
    assert public.authenticated


def test_export_playlist_writes_workbook(
    tmp_path: Path,
    export: PlaylistExport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_fetch(playlist_id: int, source: object, genres: object) -> PlaylistExport:
        seen["id"] = playlist_id
        seen["source"] = source
        return export

    monkeypatch.setattr("deezer_export.service.fetch_playlist", fake_fetch)
    output = tmp_path / "sous" / "dossier" / "p.xlsx"
    result = export_playlist(
        "https://www.deezer.com/playlist/908622995", output, arl="a" * 64
    )
    assert result is export
    assert seen["id"] == 908622995
    assert isinstance(seen["source"], GatewayClient)
    assert output.exists()
