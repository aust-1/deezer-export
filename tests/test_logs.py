"""Journal d'accès : le code OAuth (et toute chaîne de requête) n'y figure jamais."""

from __future__ import annotations

import http.client
import logging
import threading
import time
from typing import TYPE_CHECKING

import uvicorn
from uvicorn.logging import AccessFormatter

from deezer_export.web.app import create_app
from deezer_export.web.logs import (
    ACCESS_LOGGER,
    StripQueryStringFilter,
    install_access_log_filter,
)

if TYPE_CHECKING:
    import pytest
    from conftest import FakeBackend

    from deezer_export.web.config import Settings

SECRET_CODE = "fr1c4d3-oauth-secret"
CALLBACK = f"/connexion/deezer/retour?code={SECRET_CODE}&state=etat-secret"


def access_record(path: str) -> logging.LogRecord:
    """Une ligne du journal d'accès, telle qu'uvicorn la produit."""
    return logging.LogRecord(
        ACCESS_LOGGER,
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", 303),
        None,
    )


def test_filter_strips_query_string() -> None:
    record = access_record(CALLBACK)
    assert StripQueryStringFilter().filter(record) is True
    line = AccessFormatter(use_colors=False).format(record)
    assert '"GET /connexion/deezer/retour HTTP/1.1" 303' in line
    assert SECRET_CODE not in line
    assert "state" not in line


def test_filter_leaves_other_records_alone() -> None:
    plain = access_record("/exports/abc")
    StripQueryStringFilter().filter(plain)
    assert plain.args == ("127.0.0.1:5000", "GET", "/exports/abc", "1.1", 303)

    other = logging.LogRecord("x", logging.INFO, __file__, 1, "%s", ("a?b",), None)
    StripQueryStringFilter().filter(other)
    assert other.args == ("a?b",)


def test_filter_is_installed_once() -> None:
    install_access_log_filter()
    install_access_log_filter()
    filters = logging.getLogger(ACCESS_LOGGER).filters
    assert sum(isinstance(f, StripQueryStringFilter) for f in filters) == 1


def test_real_server_does_not_log_oauth_code(
    settings: Settings, backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    """Uvicorn réel : le retour OAuth est journalisé sans son code."""
    app = create_app(settings, backend=backend)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", CALLBACK)
            assert conn.getresponse().status == 303
            conn.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    lines = [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER]
    assert any("GET /connexion/deezer/retour HTTP/1.1" in line for line in lines)
    assert not any(SECRET_CODE in line or "state=" in line for line in lines)
