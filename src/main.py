import csv
import time
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import requests

API = "https://api.deezer.com"
GENRE_COLUMNS = 5


def _get_json(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _fetch_tracks(playlist_id: str) -> list[dict]:
    tracks: list[dict] = []
    url: str | None = f"{API}/playlist/{playlist_id}/tracks"
    params: dict | None = {"limit": 100}

    while url:
        data = _get_json(url, params)
        tracks.extend(data["data"])
        url = data.get("next")
        params = None

    return tracks


def _build_genre_cache(album_ids: set[int]) -> dict[int, list[str]]:
    cache: dict[int, list[str]] = {}

    for album_id in album_ids:
        album = _get_json(f"{API}/album/{album_id}")
        cache[album_id] = [g["name"] for g in album.get("genres", {}).get("data", [])]
        time.sleep(0.05)  # ménage le rate limit (~50 req/10 s)

    return cache


def export_playlist(playlist_id: str, output: str = "playlist.csv") -> None:
    tracks = _fetch_tracks(playlist_id)
    album_ids = {t["album"]["id"] for t in tracks}
    genre_cache = _build_genre_cache(album_ids)

    header = ["title", "artist", "album", "duration_s", "link"]
    header += [f"genre {i}" for i in range(1, GENRE_COLUMNS + 1)]

    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        for t in tracks:
            genres = genre_cache.get(t["album"]["id"], [])
            genres = (genres + [""] * GENRE_COLUMNS)[:GENRE_COLUMNS]
            writer.writerow(
                [
                    t["title"],
                    t["artist"]["name"],
                    t["album"]["title"],
                    t["duration"],
                    t["link"],
                    *genres,
                ]
            )

    print(
        f"{len(tracks)} pistes exportées vers {output} "
        f"({len(album_ids)} albums interrogés)"
    )


if __name__ == "__main__":
    print("Exportation de la playlist Deezer vers un fichier CSV...")
    export_playlist(
        "10016300382",
        f"output/playlist_{dt.now(ZoneInfo('Europe/Paris')).date().isoformat()}.csv",
    )
