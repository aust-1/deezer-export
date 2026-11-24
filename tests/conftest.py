"""Fixtures communes : données de playlist, faux accès Deezer, client HTTP du site."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from deezer_export.deezer.errors import AuthenticationError, DeezerError
from deezer_export.deezer.http import RateLimiter
from deezer_export.models import PlaylistExport, PlaylistInfo, Track, User
from deezer_export.web.app import create_app
from deezer_export.web.config import Settings
from deezer_export.web.jobs import JobManager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from deezer_export.web.sessions import DeezerAuth

PARIS = ZoneInfo("Europe/Paris")
ARL = "a1b2" * 48  # 192 caractères, comme un vrai cookie arl


@pytest.fixture
def no_limit() -> RateLimiter:
    """Limiteur qui n'attend jamais."""
    return RateLimiter(max_calls=10_000, period=1.0, sleep=lambda _s: None)


def make_track(
    n: int, *, artist: str = "Artiste", album_id: int = 1, duration: int = 180
) -> Track:
    """Fabrique une piste de test."""
    return Track(
        title=f"Titre {n}",
        artist=artist,
        album=f"Album {album_id}",
        album_id=album_id,
        duration=duration,
        link=f"https://www.deezer.com/track/{n}",
    )


PLAYLIST = PlaylistInfo(
    id=908622995,
    title="En mode 60",
    nb_tracks=4,
    public=True,
    owner="Alexandre",
    link="https://www.deezer.com/playlist/908622995",
    picture="https://cdn-images.dzcdn.net/images/playlist/x/250x250-000000-80-0-0.jpg",
)


@pytest.fixture
def export() -> PlaylistExport:
    """Une petite playlist : 4 pistes, 3 albums, 2 artistes."""
    tracks = (
        make_track(1, artist="The Beatles", album_id=10, duration=429),
        make_track(2, artist="The Beatles", album_id=10, duration=150),
        make_track(3, artist="Aretha Franklin", album_id=20, duration=200),
        make_track(4, artist="Aretha Franklin", album_id=30, duration=100),
    )
    return PlaylistExport(
        playlist=PLAYLIST,
        tracks=tracks,
        genres={10: ("Rock", "Pop"), 20: ("R&B",), 30: ()},
        generated_at=datetime(2026, 11, 13, 10, 30, tzinfo=PARIS),
        albums_queried=3,
    )


class FakeBackend:
    """Accès Deezer simulé, sans réseau."""

    def __init__(self, export: PlaylistExport) -> None:
        self.export = export
        self.fail_with: DeezerError | None = None
        self.calls: list[tuple[int, DeezerAuth | None]] = []
        self.playlists_error: DeezerError | None = None

    def fetch(
        self,
        playlist_id: int,
        auth: DeezerAuth | None,
        progress: Callable[[str, int, int], None],
    ) -> PlaylistExport:
        self.calls.append((playlist_id, auth))
        progress("albums", 1, 3)
        if self.fail_with:
            raise self.fail_with
        return self.export

    def my_playlists(self, auth: DeezerAuth) -> list[PlaylistInfo]:
        if self.playlists_error:
            raise self.playlists_error
        assert auth.user.name == "aust-1"
        return [PLAYLIST]

    def login_arl(self, arl: str) -> User:
        if arl != ARL:
            msg = "Ce cookie arl n'ouvre aucune session Deezer."
            raise AuthenticationError(msg)
        return User(id=42, name="aust-1")

    def authorize_url(self, state: str) -> str:
        return f"https://connect.deezer.com/oauth/auth.php?state={state}"

    def login_oauth(self, code: str) -> tuple[str, int | None, User]:
        if code != "bon-code":
            msg = "Deezer a refusé la connexion."
            raise AuthenticationError(msg)
        return "jeton", 3600, User(id=42, name="aust-1")


@pytest.fixture
def backend(export: PlaylistExport) -> FakeBackend:
    """Le faux accès Deezer."""
    return FakeBackend(export)


@pytest.fixture
def settings() -> Settings:
    """Réglages de test : OAuth et arl activés, en HTTP."""
    return Settings(deezer_app_id="123", deezer_app_secret="secret")


@pytest.fixture
def client(settings: Settings, backend: FakeBackend) -> Iterator[TestClient]:
    """Client HTTP du site."""
    app = create_app(
        settings, backend=backend, jobs=JobManager(workers=1, max_pending=2)
    )
    with TestClient(app, follow_redirects=False) as c:
        yield c


def csrf_of(client: TestClient) -> str:
    """Charge l'accueil et renvoie le jeton CSRF de la session."""
    html = client.get("/").text
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def wait_job(
    client: TestClient, location: str, timeout: float = 5.0
) -> dict[str, object]:
    """Attend la fin d'un export et renvoie son dernier état."""
    deadline = time.monotonic() + timeout
    while True:
        state: dict[str, object] = client.get(f"{location}/etat").json()
        if state["status"] in {"done", "error"} or time.monotonic() > deadline:
            return state
        time.sleep(0.02)
