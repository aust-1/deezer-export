"""OAuth Deezer et limiteur de débit."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from deezer_export.deezer.errors import AuthenticationError, DeezerError
from deezer_export.deezer.http import USER_AGENT, RateLimiter, make_session
from deezer_export.deezer.oauth import CONNECT, OAuthToken, authorize_url, exchange_code


def test_authorize_url() -> None:
    url = urlparse(authorize_url("123", "https://exemple.fr/retour?state=abc"))
    assert url.netloc == "connect.deezer.com"
    query = parse_qs(url.query)
    assert query == {
        "app_id": ["123"],
        "redirect_uri": ["https://exemple.fr/retour?state=abc"],
        "perms": ["basic_access"],
    }


@responses.activate
def test_exchange_code_json() -> None:
    responses.get(
        f"{CONNECT}/access_token.php", json={"access_token": "tok", "expires": 3600}
    )
    token = exchange_code("1", "s", "code")
    assert token == OAuthToken(access_token="tok", expires_in=3600)
    assert "tok" not in repr(token)


@responses.activate
def test_exchange_code_query_string_without_expiry() -> None:
    responses.get(f"{CONNECT}/access_token.php", body="access_token=tok&expires=0")
    assert exchange_code("1", "s", "code").expires_in is None


@responses.activate
def test_exchange_code_refused() -> None:
    responses.get(f"{CONNECT}/access_token.php", body="wrong code")
    with pytest.raises(AuthenticationError):
        exchange_code("1", "s", "bad")


@responses.activate
def test_exchange_code_network_error() -> None:
    responses.get(f"{CONNECT}/access_token.php", body=requests.Timeout())
    with pytest.raises(DeezerError, match="injoignable"):
        exchange_code("1", "s", "code")


def test_rate_limiter_waits_when_window_is_full() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(max_calls=2, period=5.0, clock=lambda: now[0], sleep=sleep)
    limiter.acquire()
    limiter.acquire()
    assert waits == []
    limiter.acquire()  # 3e appel : attend que le 1er sorte de la fenêtre
    assert waits == [5.0]


def test_session_identifies_itself() -> None:
    assert make_session().headers["User-Agent"] == USER_AGENT
