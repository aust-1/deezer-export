"""Client de l'API publique : pagination, erreurs Deezer, reprises."""

from __future__ import annotations

import pytest
import requests
import responses

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    QuotaExceededError,
)
from deezer_export.deezer.http import RateLimiter
from deezer_export.deezer.public import API, PublicClient

TRACK = {
    "id": 116348632,
    "title": "Hey Jude (Remastered 2015)",
    "duration": 429,
    "link": "https://www.deezer.com/track/116348632",
    "artist": {"name": "The Beatles"},
    "album": {"id": 12047956, "title": "1 (Remastered 2015)"},
}


def client(
    no_limit: RateLimiter, token: str | None = None, retries: int = 2
) -> PublicClient:
    return PublicClient(
        token, limiter=no_limit, sleep=lambda _s: None, max_retries=retries
    )


@responses.activate
def test_playlist_tracks_follow_pagination(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/playlist/7/tracks",
        json={"data": [TRACK] * 100, "next": "…index=100"},
        match=[responses.matchers.query_param_matcher({"limit": "100", "index": "0"})],
    )
    responses.get(
        f"{API}/playlist/7/tracks",
        json={"data": [TRACK] * 3},
        match=[
            responses.matchers.query_param_matcher({"limit": "100", "index": "100"})
        ],
    )
    tracks = client(no_limit).playlist_tracks(7)
    assert len(tracks) == 103
    assert tracks[0].title == "Hey Jude (Remastered 2015)"
    assert tracks[0].album_id == 12047956
    assert tracks[0].duration == 429


@responses.activate
def test_access_token_is_sent_on_every_page(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/user/me/playlists",
        json={
            "data": [
                {
                    "id": 5,
                    "title": "Privée",
                    "public": False,
                    "nb_tracks": 2,
                    "creator": {"name": "moi"},
                }
            ]
        },
    )
    playlists = client(no_limit, token="tok").my_playlists()
    assert playlists[0].public is False
    assert playlists[0].owner == "moi"
    assert "access_token=tok" in (responses.calls[0].request.url or "")


@responses.activate
def test_playlist_metadata(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/playlist/7",
        json={
            "id": 7,
            "title": "T",
            "nb_tracks": 3,
            "public": True,
            "creator": {"name": "C"},
            "picture_medium": "https://cdn-images.dzcdn.net/x.jpg",
        },
    )
    info = client(no_limit).playlist(7)
    assert (info.title, info.nb_tracks, info.owner) == ("T", 3, "C")
    assert info.link == "https://www.deezer.com/playlist/7"
    assert info.picture == "https://cdn-images.dzcdn.net/x.jpg"


@responses.activate
def test_private_playlist_without_token(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/playlist/7",
        json={"error": {"type": "OAuthException", "message": "x", "code": 200}},
    )
    with pytest.raises(PrivatePlaylistError, match="connecte-toi"):
        client(no_limit).playlist(7)


@responses.activate
def test_private_playlist_of_someone_else(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/playlist/7",
        json={"error": {"type": "OAuthException", "message": "x", "code": 200}},
    )
    with pytest.raises(PrivatePlaylistError, match="n'appartient pas"):
        client(no_limit, token="tok").playlist(7)


@responses.activate
def test_not_found_hints_at_login_only_when_anonymous(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/playlist/1",
        json={"error": {"type": "DataException", "message": "no data", "code": 800}},
    )
    with pytest.raises(PlaylistNotFoundError, match="Si elle est privée"):
        client(no_limit).playlist(1)
    with pytest.raises(PlaylistNotFoundError) as exc:
        client(no_limit, token="t").playlist(1)
    assert "privée" not in str(exc.value)


@responses.activate
def test_expired_token(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/user/me",
        json={"error": {"type": "OAuthException", "message": "Invalid", "code": 300}},
    )
    with pytest.raises(AuthenticationError):
        client(no_limit, token="t").me()


@responses.activate
def test_unknown_error_is_reported(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/user/me",
        json={"error": {"type": "ParameterException", "message": "bad", "code": 500}},
    )
    with pytest.raises(DeezerError, match="bad"):
        client(no_limit).me()


@responses.activate
def test_quota_is_retried_then_succeeds(no_limit: RateLimiter) -> None:
    quota = {
        "error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}
    }
    responses.get(f"{API}/user/me", json=quota)
    responses.get(f"{API}/user/me", json={"id": 1, "name": "moi"})
    assert client(no_limit).me().name == "moi"
    assert len(responses.calls) == 2


@responses.activate
def test_quota_exhausted(no_limit: RateLimiter) -> None:
    responses.get(f"{API}/user/me", json={"error": {"code": 4, "message": "Quota"}})
    with pytest.raises(QuotaExceededError):
        client(no_limit, retries=1).me()
    assert len(responses.calls) == 2


@responses.activate
def test_cdn_block_is_retried_then_reported(no_limit: RateLimiter) -> None:
    responses.get(f"{API}/user/me", body="<HTML>Access Denied</HTML>", status=403)
    with pytest.raises(QuotaExceededError, match="bloque"):
        client(no_limit, retries=2).me()
    assert len(responses.calls) == 3


@responses.activate
def test_server_error_then_unreadable(no_limit: RateLimiter) -> None:
    responses.get(f"{API}/user/me", body="oops", status=502)
    with pytest.raises(DeezerError, match="illisible"):
        client(no_limit, retries=1).me()


@responses.activate
def test_non_object_json(no_limit: RateLimiter) -> None:
    responses.get(f"{API}/user/me", json=[1, 2])
    with pytest.raises(DeezerError, match="inattendue"):
        client(no_limit).me()


@responses.activate
def test_network_error(no_limit: RateLimiter) -> None:
    responses.get(f"{API}/user/me", body=requests.ConnectionError("down"))
    with pytest.raises(DeezerError, match="injoignable"):
        client(no_limit, retries=1).me()


@responses.activate
def test_album_genres(no_limit: RateLimiter) -> None:
    responses.get(
        f"{API}/album/3", json={"genres": {"data": [{"name": "Pop"}, {"name": "Rock"}]}}
    )
    responses.get(f"{API}/album/4", json={"error": {"code": 800, "message": "no data"}})
    c = client(no_limit)
    assert c.album_genres(3) == ("Pop", "Rock")
    assert c.album_genres(4) == ()
