"""Site web : sécurité, export, connexion, sessions et file d'exports."""

from __future__ import annotations

import time
from io import BytesIO
from typing import TYPE_CHECKING

import pytest
from conftest import ARL, PLAYLIST, FakeBackend, csrf_of, wait_job
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from deezer_export.deezer.errors import (
    AuthenticationError,
    DeezerError,
    PrivatePlaylistError,
)
from deezer_export.models import User
from deezer_export.web.app import content_security_policy, create_app
from deezer_export.web.config import Settings
from deezer_export.web.jobs import JobManager, TooManyJobsError
from deezer_export.web.sessions import COOKIE_NAME, DeezerAuth, SessionStore

if TYPE_CHECKING:
    from deezer_export.models import PlaylistExport


# --- Pages et en-têtes -----------------------------------------------------------------------


def test_home_sets_session_cookie_and_security_headers(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Deezer<br />Export" in resp.text
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie  # servi en HTTP dans les tests
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "strict-transport-security" not in resp.headers
    # Le cookie n'est posé qu'une fois.
    assert "set-cookie" not in client.get("/").headers


def test_https_settings_secure_cookie_and_hsts(backend: FakeBackend) -> None:
    app = create_app(Settings(public_url="https://deezer.exemple.fr"), backend=backend)
    with TestClient(app) as c:
        resp = c.get("/")
    assert "Secure" in resp.headers["set-cookie"]
    assert "max-age=63072000" in resp.headers["strict-transport-security"]


def test_static_files_and_health_have_no_session(client: TestClient) -> None:
    resp = client.get("/static/css/site.css")
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers
    assert resp.headers["cache-control"] == "public, max-age=86400"
    health = client.get("/healthz")
    assert health.text == "ok"
    assert "set-cookie" not in health.headers


def test_unknown_page(client: TestClient) -> None:
    resp = client.get("/nope")
    assert resp.status_code == 404
    assert "Introuvable" in resp.text


def test_csp_allows_analytics_host_only_when_configured() -> None:
    assert "analytics" not in content_security_policy(Settings())
    csp = content_security_policy(
        Settings(
            analytics_script="https://analytics.exemple.fr/api/script.js",
            analytics_site_id="x",
        )
    )
    assert "script-src 'self' https://analytics.exemple.fr" in csp


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_URL", "https://exemple.fr/")
    monkeypatch.setenv("ARL_LOGIN", "false")
    monkeypatch.setenv("DEEZER_APP_ID", "1")
    monkeypatch.setenv("DEEZER_APP_SECRET", "")
    monkeypatch.setenv("EXPORT_WORKERS", "3")
    s = Settings.from_env()
    assert s.public_url == "https://exemple.fr"
    assert s.arl_login is False
    assert s.oauth_enabled is False
    assert s.workers == 3
    assert s.oauth_redirect_uri == "https://exemple.fr/connexion/deezer/retour"
    assert "secret" not in repr(Settings(deezer_app_secret="secret"))


# --- Export ----------------------------------------------------------------------------------


def test_export_flow(client: TestClient, backend: FakeBackend) -> None:
    csrf = csrf_of(client)
    resp = client.post(
        "/exports",
        data={"csrf": csrf, "playlist": "https://www.deezer.com/fr/playlist/908622995"},
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/exports/")

    state = wait_job(client, location)
    assert state["status"] == "done"
    assert state["percent"] == 100
    assert backend.calls == [(908622995, None)]

    page = client.get(location)
    assert page.status_code == 200
    assert "En mode 60" in page.text
    assert "Télécharger le classeur" in page.text
    assert "The Beatles" in page.text

    xlsx = client.get(f"{location}/classeur.xlsx")
    assert xlsx.status_code == 200
    assert xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert (
        'filename="playlist_908622995_2026-11-13.xlsx"'
        in xlsx.headers["content-disposition"]
    )
    assert load_workbook(BytesIO(xlsx.content)).sheetnames == [
        "Playlist",
        "Statistiques",
    ]


def test_progress_page_while_running(client: TestClient, backend: FakeBackend) -> None:
    started: list[int] = []
    release: list[bool] = []

    def slow(playlist_id: int, auth: object, progress: object) -> PlaylistExport:
        started.append(playlist_id)
        while not release:
            time.sleep(0.01)
        return backend.export

    backend.fetch = slow  # type: ignore[method-assign]
    location = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": "1"}
    ).headers["location"]
    page = client.get(location)
    assert "Analyse" in page.text
    assert 'http-equiv="refresh"' in page.text
    assert client.get(f"{location}/classeur.xlsx").status_code == 404
    release.append(True)
    assert wait_job(client, location)["status"] == "done"


def test_export_errors_are_shown(client: TestClient, backend: FakeBackend) -> None:
    backend.fail_with = PrivatePlaylistError(
        "Cette playlist est privée : connecte-toi à Deezer pour l'exporter."
    )
    location = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": "7"}
    ).headers["location"]
    assert wait_job(client, location)["status"] == "error"
    page = client.get(location)
    assert page.status_code == 502
    assert "connecte-toi" in page.text


def test_unexpected_errors_are_hidden(client: TestClient, backend: FakeBackend) -> None:
    backend.fail_with = RuntimeError("détail interne")  # type: ignore[assignment]
    location = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": "7"}
    ).headers["location"]
    wait_job(client, location)
    page = client.get(location)
    assert "détail interne" not in page.text
    assert "Erreur inattendue" in page.text


def test_export_requires_csrf(client: TestClient, backend: FakeBackend) -> None:
    client.get("/")
    resp = client.post("/exports", data={"csrf": "faux", "playlist": "1"})
    assert resp.status_code == 403
    assert backend.calls == []


def test_invalid_playlist_is_flashed(client: TestClient) -> None:
    resp = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": "https://evil.example/x"}
    )
    assert resp.headers["location"] == "/#exporter"
    home = client.get("/")
    assert "Colle l&#39;ID" in home.text
    assert "Colle l&#39;ID" not in client.get("/").text  # affiché une seule fois


def test_jobs_belong_to_their_session(
    client: TestClient, settings: Settings, backend: FakeBackend
) -> None:
    location = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": "1"}
    ).headers["location"]
    wait_job(client, location)
    with TestClient(client.app) as stranger:
        assert stranger.get(location).status_code == 404
        assert stranger.get(f"{location}/classeur.xlsx").status_code == 404
        assert stranger.get(f"{location}/etat").status_code == 404


# --- Connexion -------------------------------------------------------------------------------


def login_arl(client: TestClient) -> None:
    resp = client.post("/connexion/arl", data={"csrf": csrf_of(client), "arl": ARL})
    assert resp.headers["location"] == "/#mes-playlists"


def test_arl_login_lists_playlists_and_exports_with_auth(
    client: TestClient, backend: FakeBackend
) -> None:
    old_cookie = client.get("/").cookies.get(COOKIE_NAME) or client.cookies.get(
        COOKIE_NAME
    )
    login_arl(client)
    assert client.cookies.get(COOKIE_NAME) != old_cookie  # rotation de session

    home = client.get("/")
    assert "Mes playlists" in home.text
    assert "Déconnexion" in home.text
    assert ARL not in home.text

    location = client.post(
        "/exports", data={"csrf": csrf_of(client), "playlist": str(PLAYLIST.id)}
    ).headers["location"]
    wait_job(client, location)
    _, auth = backend.calls[0]
    assert auth is not None
    assert (auth.kind, auth.secret) == ("arl", ARL)


def test_arl_login_rejects_bad_values(client: TestClient) -> None:
    client.post("/connexion/arl", data={"csrf": csrf_of(client), "arl": "trop court"})
    assert "pas un cookie arl" in client.get("/").text
    client.post("/connexion/arl", data={"csrf": csrf_of(client), "arl": "b" * 192})
    assert "aucune session Deezer" in client.get("/").text
    assert (
        client.post("/connexion/arl", data={"csrf": "x", "arl": ARL}).status_code == 403
    )


def test_arl_login_can_be_disabled(backend: FakeBackend) -> None:
    app = create_app(Settings(arl_login=False), backend=backend)
    with TestClient(app) as c:
        assert "cookie arl" not in c.get("/").text.lower().replace(
            'cookie <span class="t-mono">arl', ""
        )
        assert c.post("/connexion/arl", data={"arl": ARL}).status_code == 404
        assert c.get("/connexion/deezer").status_code == 404


def test_playlists_errors(client: TestClient, backend: FakeBackend) -> None:
    login_arl(client)
    backend.playlists_error = DeezerError(
        "Deezer est injoignable pour le moment, réessaie plus tard."
    )
    assert "injoignable" in client.get("/").text
    backend.playlists_error = AuthenticationError(
        "Ta session Deezer a expiré : reconnecte-toi pour continuer."
    )
    assert "a expiré" in client.get("/").text
    assert "Connexion" in client.get("/").text  # déconnecté d'office


def test_logout(client: TestClient) -> None:
    login_arl(client)
    resp = client.post("/deconnexion", data={"csrf": csrf_of(client)})
    assert resp.headers["location"] == "/"
    assert "Mes playlists" not in client.get("/").text.split("<main", 1)[1]
    assert client.post("/deconnexion", data={"csrf": "x"}).status_code == 403


def test_oauth_flow(client: TestClient, backend: FakeBackend) -> None:
    start = client.get("/connexion/deezer")
    assert start.status_code == 303
    state = start.headers["location"].rsplit("state=", 1)[1]

    bad = client.get(
        "/connexion/deezer/retour", params={"code": "bon-code", "state": "autre"}
    )
    assert bad.headers["location"] == "/#connexion"
    assert "expirée" in client.get("/").text

    state = client.get("/connexion/deezer").headers["location"].rsplit("state=", 1)[1]
    ok = client.get(
        "/connexion/deezer/retour", params={"code": "bon-code", "state": state}
    )
    assert ok.headers["location"] == "/#mes-playlists"
    assert "Mes playlists" in client.get("/").text

    # Le state ne sert qu'une fois.
    again = client.get(
        "/connexion/deezer/retour", params={"code": "bon-code", "state": state}
    )
    assert again.headers["location"] == "/#connexion"


def test_oauth_cancelled_or_refused(client: TestClient) -> None:
    state = client.get("/connexion/deezer").headers["location"].rsplit("state=", 1)[1]
    client.get(
        "/connexion/deezer/retour",
        params={"state": state, "error_reason": "user_denied"},
    )
    assert "annulée" in client.get("/").text
    state = client.get("/connexion/deezer").headers["location"].rsplit("state=", 1)[1]
    client.get("/connexion/deezer/retour", params={"state": state, "code": "mauvais"})
    assert "refusé" in client.get("/").text


# --- Sessions et file ------------------------------------------------------------------------


def test_session_store_expiry_rotation_and_auth_expiry() -> None:
    now = [0.0]
    store = SessionStore(ttl_seconds=10, max_sessions=2, clock=lambda: now[0])
    s = store.load(None)
    assert s.is_new
    assert store.load(s.id) is s
    assert not s.is_new

    s.auth = DeezerAuth(kind="oauth", secret="t", user=User(1, "u"), expires_at=5.0)
    old_id, owner = s.id, s.owner
    store.rotate(s)
    assert s.id != old_id
    assert s.owner == owner
    assert store.load(old_id) is not s

    now[0] = 6.0
    assert store.load(s.id).auth is None  # jeton OAuth expiré

    now[0] = 100.0
    assert store.load(s.id) is not s  # session expirée
    store.load(None)
    store.load(None)
    assert len(store) == 2  # les plus anciennes sautent
    assert "secret" not in repr(
        DeezerAuth(kind="arl", secret="secret", user=User(1, "u"))
    )


def test_job_manager_limits(export: PlaylistExport) -> None:
    release: list[bool] = []

    def work(progress: object) -> PlaylistExport:
        while not release:
            time.sleep(0.01)
        return export

    jobs = JobManager(workers=1, max_pending=2)
    try:
        first = jobs.submit("a", 1, work)
        assert jobs.submit("a", 1, work) is first  # même playlist : même export
        with pytest.raises(TooManyJobsError, match="déjà en cours"):
            jobs.submit("a", 2, work)
        jobs.submit("b", 1, work)
        with pytest.raises(TooManyJobsError, match="très demandé"):
            jobs.submit("c", 1, work)
        assert jobs.get(first.id, "b") is None
    finally:
        release.append(True)
        jobs.shutdown()
