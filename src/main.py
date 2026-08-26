import csv
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import requests


def export_playlist(playlist_id: str, output: str = "playlist.csv") -> None:
    url = f"https://api.deezer.com/playlist/{playlist_id}/tracks"
    tracks: list[dict] = []

    while url:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tracks.extend(data["data"])
        url = data.get("next")

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "artist", "album", "duration_s", "isrc", "link"])
        for t in tracks:
            writer.writerow(
                [
                    t["title"],
                    t["artist"]["name"],
                    t["album"]["title"],
                    t["duration"],
                    t.get("isrc", ""),
                    t["link"],
                ]
            )

    print(f"{len(tracks)} pistes exportées vers {output}")


if __name__ == "__main__":
    print("Exportation de la playlist Deezer vers un fichier CSV...")
    export_playlist(
        "10016300382",
        f"output/playlist_{dt.now(ZoneInfo('Europe/Paris')).date().isoformat()}.csv",
    )
