"""Le site dans un vrai navigateur (Playwright, Chromium), avec un faux accès Deezer.

Lancement : ``uv sync --group e2e``, ``uv run playwright install chromium``, puis
``uv run pytest -m e2e``. Aucun appel réseau ne sort de la machine : Deezer est simulé
par ``FakeBackend`` et toute requête vers un autre hôte que le serveur de test est
bloquée (et fait échouer le test).

Les captures (clair, sombre, mobile) sont écrites dans ``test-results/screenshots``,
gardées comme artefacts par la CI.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api", reason="groupe e2e non installé")

import uvicorn
from conftest import PARIS, PLAYLIST, FakeBackend, make_track
from openpyxl import load_workbook
from playwright.sync_api import expect

from deezer_export.models import PlaylistExport
from deezer_export.web.app import create_app
from deezer_export.web.config import Settings
from deezer_export.web.jobs import JobManager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import (
        BrowserContext,
        ConsoleMessage,
        Page,
        Route,
        ViewportSize,
    )

    from deezer_export.web.sessions import DeezerAuth

pytestmark = pytest.mark.e2e

SCREENSHOTS = Path("test-results/screenshots")
MOBILE: ViewportSize = {"width": 390, "height": 844}
# Les violations CSP passent par la console : elles comptent comme erreurs.
REPORT_CSP = """
document.addEventListener('securitypolicyviolation', (e) => {
  console.error('CSP: ' + e.violatedDirective + ' ' + e.blockedURI);
});
"""


class SlowBackend(FakeBackend):
    """Faux accès Deezer dont l'export dure assez pour voir la page d'avancement."""

    def fetch(
        self,
        playlist_id: int,
        auth: DeezerAuth | None,
        progress: Callable[[str, int, int], None],
    ) -> PlaylistExport:
        self.calls.append((playlist_id, auth))
        progress("tracks", 0, len(self.export.tracks))
        time.sleep(1.5)
        for done in range(1, 4):
            progress("albums", done, 3)
            time.sleep(0.5)
        return self.export


@pytest.fixture(scope="module")
def live_server(export_for_browser: PlaylistExport) -> Iterator[str]:
    """Le site, servi par un vrai uvicorn sur un port libre de 127.0.0.1."""
    backend = SlowBackend(export_for_browser)
    app = create_app(
        Settings(), backend=backend, jobs=JobManager(workers=2, max_pending=8)
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("le serveur de test n'a pas démarré")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def export_for_browser() -> PlaylistExport:
    """Une playlist sans pochette externe : la page ne charge que le site."""
    tracks = tuple(
        make_track(n, artist=f"Artiste {n % 3}", album_id=10 + n % 3, duration=150 + n)
        for n in range(1, 13)
    )
    return PlaylistExport(
        playlist=replace(PLAYLIST, picture=None),
        tracks=tracks,
        genres={10: ("Rock", "Pop"), 11: ("Jazz",), 12: ()},
        generated_at=datetime(2026, 11, 13, 10, 30, tzinfo=PARIS),
        albums_queried=3,
    )


@pytest.fixture
def browser_context_args(
    browser_context_args: dict[str, object], live_server: str
) -> dict[str, object]:
    """Contexte du navigateur : site de test, téléchargements acceptés, français."""
    return {
        **browser_context_args,
        "base_url": live_server,
        "accept_downloads": True,
        "locale": "fr-FR",
        "color_scheme": "light",
    }


class Problems:
    """Erreurs de console, erreurs JS, violations CSP et requêtes vers l'extérieur."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def console(self, message: ConsoleMessage) -> None:
        if message.type in {"error", "warning"}:
            self.messages.append(f"console {message.type}: {message.text}")

    def page_error(self, error: Exception) -> None:
        self.messages.append(f"erreur JS : {error}")


def watch(context: BrowserContext, page: Page, base_url: str) -> Problems:
    """Branche la surveillance sur une page et bloque tout hôte autre que le site."""
    problems = Problems()
    host = urlparse(base_url).netloc

    def only_local(route: Route) -> None:
        if urlparse(route.request.url).netloc == host:
            route.continue_()
        else:
            problems.messages.append(f"requête externe bloquée : {route.request.url}")
            route.abort()

    context.route("**/*", only_local)
    context.add_init_script(REPORT_CSP)
    page.on("console", problems.console)
    page.on("pageerror", problems.page_error)
    return problems


@pytest.fixture
def problems(
    context: BrowserContext, page: Page, live_server: str
) -> Iterator[Problems]:
    """Surveillance de la page ; le test échoue si un problème est relevé."""
    found = watch(context, page, live_server)
    yield found
    assert found.messages == []


def shoot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}.png", full_page=True)


def run_export(page: Page) -> None:
    """Lance un export depuis l'accueil et attend la page de résultats."""
    page.goto("/")
    page.get_by_label("ID ou lien de la playlist").fill(
        "https://www.deezer.com/fr/playlist/908622995"
    )
    page.get_by_role("button", name="Analyser").click()

    # Page d'avancement, suivie en JS (pas de rafraîchissement <noscript>)…
    expect(page.get_by_role("heading", name="Analyse en cours")).to_be_visible()
    expect(page.locator("[data-step]")).to_have_text("genres des albums", timeout=5000)
    # … puis rechargée d'elle-même à la fin de l'export.
    expect(page.get_by_role("heading", name="En mode 60.")).to_be_visible(
        timeout=15_000
    )


def test_export_until_results_and_download(page: Page, problems: Problems) -> None:
    run_export(page)
    expect(page.get_by_role("link", name="Titre 12")).to_be_visible()
    shoot(page, "resultats-clair")

    with page.expect_download() as info:
        page.get_by_role("link", name="Télécharger le classeur .xlsx").click()
    download = info.value
    assert download.suggested_filename == "En mode 60 2026-11-13.xlsx"
    path = download.path()
    workbook = load_workbook(BytesIO(Path(path).read_bytes()))
    assert workbook.sheetnames == ["Playlist", "Statistiques"]
    assert workbook["Playlist"].max_row == 13  # en-tête + 12 pistes


def test_theme_toggle_is_remembered(page: Page, problems: Problems) -> None:
    page.goto("/")
    root = page.locator("html")
    toggle = page.get_by_role("button", name="Passer en thème sombre")
    expect(toggle).to_be_visible()
    toggle.click()
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('er-theme')") == "dark"

    page.reload()  # appliqué avant le premier rendu par theme-init.js
    expect(root).to_have_attribute("data-theme", "dark")
    expect(page.get_by_role("button", name="Passer en thème clair")).to_be_visible()
    run_export(page)
    expect(root).to_have_attribute("data-theme", "dark")
    shoot(page, "resultats-sombre")

    page.get_by_role("button", name="Passer en thème clair").click()
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('er-theme')") == "light"


def test_mobile_layout(page: Page, problems: Problems, context: BrowserContext) -> None:
    page.set_viewport_size(MOBILE)
    page.goto("/")
    shoot(page, "accueil-mobile")
    run_export(page)
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= MOBILE["width"], "la page déborde horizontalement sur mobile"
    shoot(page, "resultats-mobile")


def test_problems_are_detected(
    context: BrowserContext, page: Page, live_server: str
) -> None:
    """La surveillance elle-même : une violation CSP et une requête externe."""
    found = watch(context, page, live_server)
    page.goto("/")
    page.evaluate(
        """() => {
          const s = document.createElement('script');
          s.textContent = 'window.__inline = 1';
          document.head.appendChild(s);
          const img = document.createElement('img');  // hôte permis par la CSP
          img.src = 'https://cdn-images.dzcdn.net/images/test.jpg';
          document.body.appendChild(img);
        }"""
    )
    page.wait_for_timeout(500)
    assert page.evaluate("window.__inline") is None  # script inline refusé par la CSP
    joined = "\n".join(found.messages)
    assert "CSP: script-src" in joined
    assert "requête externe bloquée : https://cdn-images.dzcdn.net" in joined
