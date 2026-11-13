"""Accès réel à Deezer côté site (HTTP simulé) et lancement du serveur."""

from __future__ import annotations

import pytest
import responses

from deezer_export.deezer.gateway import GATEWAY
from deezer_export.deezer.oauth import CONNECT
from deezer_export.deezer.public import API
from deezer_export.models import User
from deezer_export.service import GENRE_CACHE
from deezer_export.web import server
from deezer_export.web.backend import DeezerBackend
from deezer_export.web.config import Settings
from deezer_export.web.sessions import DeezerAuth

SETTINGS = Settings(
    public_url="https://exemple.fr", deezer_app_id="123", deezer_app_secret="s3cret"
)
USER_DATA = {
    "error": [],
    "results": {"checkForm": "c", "USER": {"USER_ID": 42, "BLOG_NAME": "aust-1"}},
}
TRACK = {
    "id": 1,
    "title": "T",
    "duration": 60,
    "link": "l",
    "artist": {"name": "A"},
    "album": {"id": 77, "title": "Al"},
}


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    GENRE_CACHE._data.clear()


@responses.activate
def test_fetch_public_playlist() -> None:
    responses.get(
        f"{API}/playlist/5",
        json={"id": 5, "title": "P", "nb_tracks": 1, "public": True},
    )
    responses.get(f"{API}/playlist/5/tracks", json={"data": [TRACK]})
    responses.get(f"{API}/album/77", json={"genres": {"data": [{"name": "Pop"}]}})
    steps: list[str] = []
    export = DeezerBackend(SETTINGS).fetch(5, None, lambda s, _d, _t: steps.append(s))
    assert export.genres == {77: ("Pop",)}
    assert steps[0] == "playlist"


@responses.activate
def test_fetch_and_list_with_oauth_token() -> None:
    auth = DeezerAuth(kind="oauth", secret="tok", user=User(1, "u"))
    responses.get(
        f"{API}/user/me/playlists",
        json={"data": [{"id": 9, "title": "Privée", "public": False}]},
    )
    assert DeezerBackend(SETTINGS).my_playlists(auth)[0].title == "Privée"
    assert "access_token=tok" in (responses.calls[0].request.url or "")


@responses.activate
def test_arl_login_and_playlists() -> None:
    responses.post(GATEWAY, json=USER_DATA)
    backend = DeezerBackend(SETTINGS)
    assert backend.login_arl("a" * 64) == User(42, "aust-1")
    responses.replace(responses.POST, GATEWAY, json=USER_DATA)
    responses.post(
        GATEWAY, json={"error": [], "results": {"TAB": {"playlists": {"data": []}}}}
    )
    auth = DeezerAuth(kind="arl", secret="a" * 64, user=User(42, "aust-1"))
    assert backend.my_playlists(auth) == []


@responses.activate
def test_oauth_login() -> None:
    backend = DeezerBackend(SETTINGS)
    url = backend.authorize_url("etat")
    assert "app_id=123" in url
    assert "exemple.fr%2Fconnexion%2Fdeezer%2Fretour%3Fstate%3Detat" in url
    responses.get(
        f"{CONNECT}/access_token.php", json={"access_token": "tok", "expires": 0}
    )
    responses.get(f"{API}/user/me", json={"id": 7, "name": "moi"})
    assert backend.login_oauth("code") == ("tok", None, User(7, "moi"))
    assert "secret=s3cret" in (responses.calls[0].request.url or "")


def test_server_main(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setenv("PORT", "9000")
    server.main()
    assert seen["app"] == "deezer_export.web.app:create_app"
    assert seen["factory"] is True
    assert seen["port"] == 9000
    assert seen["proxy_headers"] is True
