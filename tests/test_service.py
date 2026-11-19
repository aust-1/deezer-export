"""Récupération d'une playlist, références de playlist, cache des genres."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
import requests
import responses
from conftest import PARIS, PLAYLIST, make_track

from deezer_export.deezer.gateway import GatewayClient
from deezer_export.deezer.public import PublicClient
from deezer_export.service import (
    ExportCache,
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
    def __init__(self, tracks: list[Track], info: PlaylistInfo = PLAYLIST) -> None:
        self.tracks = tracks
        self.info = info
        self.track_reads = 0

    def playlist(self, playlist_id: int) -> PlaylistInfo:
        assert playlist_id == PLAYLIST.id
        return self.info

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        self.track_reads += 1
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


def fetch_with(
    source: FakeSource, exports: ExportCache, genres: FakeGenres | None = None
) -> PlaylistExport:
    return fetch_playlist(
        PLAYLIST.id,
        source,
        genres or FakeGenres(),  # type: ignore[arg-type]
        cache=GenreCache(),
        exports=exports,
    )


def test_recent_public_export_is_reused_while_checksum_holds() -> None:
    exports = ExportCache()
    info = replace(PLAYLIST, checksum="abc")
    first_source = FakeSource([make_track(1), make_track(2, album_id=2)], info)
    first = fetch_with(first_source, exports)
    assert len(exports) == 1

    # Un autre visiteur : une seule requête (la playlist et son checksum).
    renamed = replace(info, title="Nouveau titre")
    second_source = FakeSource([], renamed)
    genres = FakeGenres()
    steps: list[str] = []
    second = fetch_playlist(
        PLAYLIST.id,
        second_source,
        genres,  # type: ignore[arg-type]
        exports=exports,
        progress=lambda step, _d, _t: steps.append(step),
    )
    assert second_source.track_reads == 0
    assert genres.asked == []
    assert steps == ["playlist"]
    assert second.tracks == first.tracks
    assert second.playlist.title == "Nouveau titre"  # métadonnées fraîches
    assert second.generated_at >= first.generated_at

    # La playlist a changé : nouvelle lecture complète, et le cache suit.
    changed = FakeSource([make_track(3)], replace(info, checksum="def"))
    assert fetch_with(changed, exports).tracks == (make_track(3),)
    assert changed.track_reads == 1
    assert exports.get(PLAYLIST.id, "def") is not None
    assert exports.get(PLAYLIST.id, "abc") is None


def test_private_or_unchecked_playlists_are_never_cached() -> None:
    exports = ExportCache()
    private = FakeSource([make_track(1)], replace(PLAYLIST, public=False, checksum="x"))
    fetch_with(private, exports)
    fetch_with(private, exports)
    assert private.track_reads == 2
    no_checksum = FakeSource([make_track(1)], replace(PLAYLIST, checksum=None))
    fetch_with(no_checksum, exports)
    assert len(exports) == 0

    # Sans cache (CLI, passerelle arl) : rien n'est gardé ni réutilisé.
    public = FakeSource([make_track(1)], replace(PLAYLIST, checksum="x"))
    fetch_playlist(PLAYLIST.id, public, FakeGenres())  # type: ignore[arg-type]
    fetch_playlist(PLAYLIST.id, public, FakeGenres())  # type: ignore[arg-type]
    assert public.track_reads == 2


def test_export_cache_expiry_and_size(export: PlaylistExport) -> None:
    public = replace(export, playlist=replace(PLAYLIST, checksum="c"))
    expired = ExportCache(ttl=-1)
    expired.put(public)
    assert expired.get(PLAYLIST.id, "c") is None
    assert len(expired) == 0

    small = ExportCache(max_tracks=6)  # 4 pistes par export
    small.put(public)
    other = replace(public, playlist=replace(public.playlist, id=2))
    small.put(other)
    assert small.get(PLAYLIST.id, "c") is None  # le plus ancien a laissé sa place
    assert small.get(2, "c") is other
    small.put(replace(public, tracks=public.tracks * 2))  # 8 pistes : trop grand
    assert small.get(PLAYLIST.id, "c") is None
