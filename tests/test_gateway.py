"""Client de la passerelle gw-light (cookie arl)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import responses

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    QuotaExceededError,
)
from deezer_export.deezer.gateway import GATEWAY, GatewayClient, track_from_gateway
from deezer_export.deezer.http import RateLimiter
from deezer_export.deezer.public import PARIS

ARL = "f" * 192
USER_DATA = {
    "error": [],
    "results": {"checkForm": "csrf-1", "USER": {"USER_ID": 42, "BLOG_NAME": "aust-1"}},
}
SONG = {
    "SNG_ID": "116348632",
    "SNG_TITLE": "Hey Jude",
    "VERSION": "(Remastered 2015)",
    "ART_NAME": "The Beatles",
    "ALB_ID": "12047956",
    "ALB_TITLE": "1 (Remastered 2015)",
    "DURATION": "429",
    "DATE_ADD": 1700000000,
    "RANK_SNG": "912345",
    "EXPLICIT_LYRICS": "1",
    "ISRC": "GBAYE0601690",
}


def method(name: str) -> list[responses.matchers.Matcher]:  # type: ignore[name-defined]
    return [
        responses.matchers.query_param_matcher({"method": name}, strict_match=False)
    ]


def gw(no_limit: RateLimiter) -> GatewayClient:
    return GatewayClient(ARL, limiter=no_limit)


@responses.activate
def test_login_sends_cookie_and_reads_user(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    user = gw(no_limit).login()
    assert (user.id, user.name) == (42, "aust-1")
    request = responses.calls[0].request
    assert f"arl={ARL}" in request.headers["Cookie"]
    assert "api_token=null" in (request.url or "")


@responses.activate
def test_invalid_arl(no_limit: RateLimiter) -> None:
    responses.post(
        GATEWAY,
        json={"error": [], "results": {"checkForm": "x", "USER": {"USER_ID": 0}}},
    )
    with pytest.raises(AuthenticationError, match="invalide ou expiré"):
        gw(no_limit).login()


@responses.activate
def test_tracks_are_paged_and_mapped(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    responses.post(
        GATEWAY,
        json={"error": [], "results": {"data": [SONG] * 1000, "total": 1001}},
        match=method("playlist.getSongs"),
    )
    responses.post(
        GATEWAY,
        json={
            "error": [],
            "results": {"data": [dict(SONG, VERSION="")], "total": 1001},
        },
        match=method("playlist.getSongs"),
    )
    tracks = gw(no_limit).playlist_tracks(9)
    assert len(tracks) == 1001
    assert tracks[0].title == "Hey Jude (Remastered 2015)"
    assert tracks[-1].title == "Hey Jude"
    assert tracks[0].album_id == 12047956
    assert tracks[0].link == "https://www.deezer.com/track/116348632"
    assert tracks[0].added_at == datetime(2023, 11, 14, 23, 13, 20, tzinfo=PARIS)
    assert (tracks[0].rank, tracks[0].explicit, tracks[0].isrc) == (
        912345,
        True,
        "GBAYE0601690",
    )
    starts = [
        json.loads(c.request.body or b"{}").get("start") for c in responses.calls[1:]
    ]
    assert starts == [0, 1000]
    assert "api_token=csrf-1" in (responses.calls[1].request.url or "")


@responses.activate
def test_playlist_metadata_and_privacy(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    data = {
        "PLAYLIST_ID": "9",
        "TITLE": "Secrète",
        "NB_SONG": 12,
        "STATUS": 1,
        "PARENT_USERNAME": "aust-1",
        "PLAYLIST_PICTURE": "abc",
        "PICTURE_TYPE": "playlist",
    }
    responses.post(
        GATEWAY,
        json={"error": [], "results": {"DATA": data}},
        match=method("deezer.pagePlaylist"),
    )
    info = gw(no_limit).playlist(9)
    assert (info.id, info.title, info.nb_tracks, info.public) == (
        9,
        "Secrète",
        12,
        False,
    )
    assert (
        info.picture
        == "https://cdn-images.dzcdn.net/images/playlist/abc/250x250-000000-80-0-0.jpg"
    )


@responses.activate
def test_my_playlists(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    tab = {
        "TAB": {
            "playlists": {
                "data": [
                    {
                        "PLAYLIST_ID": "1",
                        "TITLE": "Coups de cœur",
                        "NB_SONG": 3,
                        "STATUS": 1,
                    }
                ]
            }
        }
    }
    responses.post(
        GATEWAY, json={"error": [], "results": tab}, match=method("deezer.pageProfile")
    )
    playlists = gw(no_limit).my_playlists()
    assert playlists[0].title == "Coups de cœur"
    assert playlists[0].owner == "aust-1"
    assert playlists[0].picture is None
    body = json.loads(responses.calls[1].request.body or b"{}")
    assert body["USER_ID"] == "42"


@responses.activate
def test_expired_csrf_token_is_renewed_once(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    responses.post(
        GATEWAY,
        json={"error": {"VALID_TOKEN_REQUIRED": "Invalid CSRF token"}, "results": {}},
        match=method("deezer.pagePlaylist"),
    )
    responses.post(
        GATEWAY,
        json={"error": [], "results": {"DATA": {"PLAYLIST_ID": "9", "TITLE": "ok"}}},
        match=method("deezer.pagePlaylist"),
    )
    assert gw(no_limit).playlist(9).title == "ok"


@responses.activate
def test_token_still_refused_after_renewal(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    responses.post(
        GATEWAY,
        json={"error": {"GATEWAY_ERROR": "invalid api token"}, "results": {}},
        match=method("deezer.pagePlaylist"),
    )
    with pytest.raises(AuthenticationError):
        gw(no_limit).playlist(9)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"PERMISSION_ERROR": "42 is not allowed to access 1"}, PrivatePlaylistError),
        ({"DATA_ERROR": "playlist::getData"}, PlaylistNotFoundError),
        ({"USER_AUTH_REQUIRED": "x"}, AuthenticationError),
        ({"SOMETHING": "else"}, DeezerError),
    ],
)
@responses.activate
def test_gateway_errors(
    no_limit: RateLimiter, error: dict[str, str], expected: type[Exception]
) -> None:
    responses.post(GATEWAY, json=USER_DATA, match=method("deezer.getUserData"))
    responses.post(
        GATEWAY,
        json={"error": error, "results": {}},
        match=method("deezer.pagePlaylist"),
    )
    with pytest.raises(expected):
        gw(no_limit).playlist(1)


@responses.activate
def test_cdn_block_and_unreadable_answers(no_limit: RateLimiter) -> None:
    responses.post(GATEWAY, body="denied", status=403)
    with pytest.raises(QuotaExceededError):
        gw(no_limit).login()
    responses.replace(responses.POST, GATEWAY, body="<html>", status=200)
    with pytest.raises(DeezerError, match="illisible"):
        gw(no_limit).login()


def test_repr_never_shows_the_cookie(no_limit: RateLimiter) -> None:
    assert ARL not in repr(gw(no_limit))


def test_gateway_optional_fields() -> None:
    bare = {
        k: v
        for k, v in SONG.items()
        if k not in {"DATE_ADD", "RANK_SNG", "EXPLICIT_LYRICS", "ISRC"}
    }
    track = track_from_gateway(bare)
    assert (track.added_at, track.rank, track.explicit, track.isrc) == (
        None,
        None,
        None,
        None,
    )
    for status, explicit in ((1, True), ("3", False)):
        content = {"EXPLICIT_LYRICS_STATUS": status}
        song = dict(bare, EXPLICIT_TRACK_CONTENT=content)
        assert track_from_gateway(song).explicit is explicit
