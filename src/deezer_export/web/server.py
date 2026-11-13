"""Lancement du serveur web : ``deezer-export-web`` (variables HOST et PORT)."""

from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    """Démarre uvicorn derrière le reverse proxy (en-têtes X-Forwarded-* acceptés)."""
    load_dotenv()
    uvicorn.run(
        "deezer_export.web.app:create_app",
        factory=True,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        server_header=False,
        log_level=os.getenv("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
