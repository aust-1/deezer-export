"""Ligne de commande."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import PLAYLIST

from deezer_export import cli
from deezer_export.deezer.errors import PrivatePlaylistError

if TYPE_CHECKING:
    from pathlib import Path

    from deezer_export.models import PlaylistExport


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (cli.ENV_PLAYLIST_ID, cli.ENV_ARL, cli.ENV_ACCESS_TOKEN):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # pas de .env du dépôt


def test_exports_playlist_given_as_link(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    export: PlaylistExport,
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake(
        playlist: int, output: str, *, arl: str | None, access_token: str | None
    ) -> PlaylistExport:
        calls.append((playlist, output, arl, access_token))
        return export

    monkeypatch.setattr(cli, "export_playlist", fake)
    monkeypatch.setenv(cli.ENV_ARL, "  cookie  ")
    assert (
        cli.main(["https://www.deezer.com/fr/playlist/908622995", "-o", "x.xlsx"]) == 0
    )
    assert calls == [(908622995, "x.xlsx", "cookie", None)]
    assert (
        "4 pistes exportées vers x.xlsx (3 albums interrogés)"
        in capsys.readouterr().out
    )


def test_playlist_from_env_and_default_output(
    monkeypatch: pytest.MonkeyPatch, export: PlaylistExport
) -> None:
    outputs: list[str] = []

    def fake(_playlist: int, output: str, **_kw: object) -> PlaylistExport:
        outputs.append(output)
        return export

    monkeypatch.setattr(cli, "export_playlist", fake)
    monkeypatch.setenv(cli.ENV_PLAYLIST_ID, "123")
    assert cli.main([]) == 0
    assert outputs[0].startswith("output/playlist_123_")
    assert outputs[0].endswith(".xlsx")


def test_missing_playlist(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "Indique une playlist" in capsys.readouterr().err


def test_errors_are_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_a: object, **_kw: object) -> None:
        msg = "Cette playlist est privée : connecte-toi à Deezer pour l'exporter."
        raise PrivatePlaylistError(msg)

    monkeypatch.setattr(cli, "export_playlist", fail)
    assert cli.main(["42"]) == 1
    assert "privée" in capsys.readouterr().err
    assert cli.main(["pas une playlist"]) == 1


def test_mine_needs_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--mine"]) == 2
    assert "DEEZER_ARL" in capsys.readouterr().err


def test_mine_lists_playlists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Source:
        def my_playlists(self) -> list[object]:
            return [PLAYLIST]

    monkeypatch.setattr(cli, "make_source", lambda **_kw: (Source(), None))
    monkeypatch.setenv(cli.ENV_ACCESS_TOKEN, "tok")
    assert cli.main(["--mine"]) == 0
    assert "908622995\t    4 pistes\tpublique\tEn mode 60" in capsys.readouterr().out
