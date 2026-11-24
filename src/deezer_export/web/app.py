"""Application web : formulaire, connexion Deezer, suivi et résultats d'export."""

from __future__ import annotations

import hmac
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from deezer_export._version import __version__
from deezer_export.deezer.errors import AuthenticationError, DeezerError
from deezer_export.excel import workbook_bytes
from deezer_export.service import (
    InvalidPlaylistReferenceError,
    parse_playlist_reference,
)
from deezer_export.stats import TOP_N, compute_stats, describe_track, format_duration
from deezer_export.web.backend import DeezerBackend
from deezer_export.web.config import Settings
from deezer_export.web.jobs import JobManager, TooManyJobsError
from deezer_export.web.sessions import COOKIE_NAME, DeezerAuth, Session, SessionStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from deezer_export.models import PlaylistExport, PlaylistInfo, User
    from deezer_export.web.jobs import Job

HERE = Path(__file__).parent
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ARL_PATTERN = re.compile(r"[A-Za-z0-9]{32,512}")
PLAYLISTS_CACHE_SECONDS = 300
LOGIN_FLASH = "[connexion]"  # préfixe : message affiché dans la section Connexion
_NO_SESSION_PREFIXES = ("/static/", "/healthz")
_EXPIRED_FORM = ("Formulaire expiré", "Recharge la page puis recommence.")
_BAD_ARL = "Ce n'est pas un cookie arl : copie toute sa valeur (lettres et chiffres)."

CsrfField = Annotated[str, Form(max_length=100)]
PlaylistField = Annotated[str, Form(max_length=300)]
ArlField = Annotated[str, Form(max_length=600)]


class Backend(Protocol):
    """Ce que le site attend de Deezer (voir `DeezerBackend`)."""

    def fetch(
        self,
        playlist_id: int,
        auth: DeezerAuth | None,
        progress: Callable[[str, int, int], None],
    ) -> PlaylistExport:
        """Récupère une playlist complète.

        Args:
            playlist_id (int): L'ID de la playlist.
            auth (DeezerAuth | None): L'identité Deezer de la session.
            progress (Callable[[str, int, int], None]): Rappel d'avancement.

        Returns:
            PlaylistExport: La playlist, ses pistes et leurs genres.
        """
        ...

    def my_playlists(self, auth: DeezerAuth) -> list[PlaylistInfo]:
        """Liste les playlists du compte connecté.

        Args:
            auth (DeezerAuth): L'identité Deezer de la session.

        Returns:
            list[PlaylistInfo]: Ses playlists.
        """
        ...

    def login_arl(self, arl: str) -> User:
        """Vérifie un cookie « arl ».

        Args:
            arl (str): La valeur du cookie.

        Returns:
            User: Le compte correspondant.
        """
        ...

    def authorize_url(self, state: str) -> str:
        """Construit l'URL d'autorisation OAuth.

        Args:
            state (str): Valeur anti-CSRF liée à la session.

        Returns:
            str: L'URL de la page d'autorisation Deezer.
        """
        ...

    def login_oauth(self, code: str) -> tuple[str, int | None, User]:
        """Échange un code OAuth contre un jeton.

        Args:
            code (str): Le code reçu sur l'URL de retour.

        Returns:
            tuple[str, int | None, User]: Jeton, durée de validité et compte.
        """
        ...


@dataclass(frozen=True, slots=True)
class Context:
    """Tout ce dont les routes ont besoin, rangé dans ``app.state.ctx``."""

    settings: Settings
    deezer: Backend
    jobs: JobManager
    sessions: SessionStore
    templates: Jinja2Templates


router = APIRouter()


# --- Outils ---------------------------------------------------------------------------


def _ctx(request: Request) -> Context:
    ctx: Context = request.app.state.ctx
    return ctx


def _session(request: Request) -> Session:
    session: Session = request.state.session
    return session


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Rend un gabarit avec le contexte commun (session, jeton CSRF).

    Args:
        request (Request): La requête.
        name (str): Le gabarit.
        context (dict[str, Any] | None): Variables propres à la page.
        status_code (int): Le statut HTTP.

    Returns:
        HTMLResponse: La page.
    """
    session: Session | None = getattr(request.state, "session", None)
    base = {
        "auth": session.auth if session else None,
        "csrf": session.csrf if session else "",
    }
    return _ctx(request).templates.TemplateResponse(
        request, name, {**base, **(context or {})}, status_code=status_code
    )


def error_page(
    request: Request,
    status: int,
    heading: str,
    message: str,
) -> HTMLResponse:
    """Rend la page d'erreur.

    Args:
        request (Request): La requête.
        status (int): Le statut HTTP.
        heading (str): Le titre affiché.
        message (str): L'explication.

    Returns:
        HTMLResponse: La page d'erreur.
    """
    context = {"heading": heading, "message": message, "label": f"erreur {status}"}
    return render(request, "error.html", context, status_code=status)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=HTTPStatus.SEE_OTHER)


def _csrf_ok(session: Session, token: str) -> bool:
    return hmac.compare_digest(session.csrf.encode(), token.encode())


def _expired_form(request: Request) -> HTMLResponse:
    return error_page(request, HTTPStatus.FORBIDDEN, *_EXPIRED_FORM)


def content_security_policy(settings: Settings) -> str:
    """Construit la politique CSP : rien d'externe hormis les pochettes Deezer.

    Args:
        settings (Settings): Les réglages du site.

    Returns:
        str: La valeur de l'en-tête ``Content-Security-Policy``.
    """
    analytics = ""
    if settings.analytics_script:
        match = re.match(r"https://[^/]+", settings.analytics_script)
        analytics = f" {match.group(0)}" if match else ""
    directives = [
        "default-src 'none'",
        f"script-src 'self'{analytics}",
        f"connect-src 'self'{analytics}",
        "style-src 'self'",
        # Seules les largeurs de barres (--w) passent par l'attribut style.
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' https://cdn-images.dzcdn.net https://e-cdns-images.dzcdn.net",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]
    return "; ".join(directives)


# --- Pages ----------------------------------------------------------------------------


@router.get("/healthz", include_in_schema=False)
def healthz() -> PlainTextResponse:
    """Sonde de santé (healthcheck Docker).

    Returns:
        PlainTextResponse: « ok ».
    """
    return PlainTextResponse("ok")


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Accueil : formulaire, connexion ou playlists du compte connecté.

    Args:
        request (Request): La requête.

    Returns:
        HTMLResponse: La page d'accueil.
    """
    session = _session(request)
    context: dict[str, Any] = {"flash": None, "flash_login": None}
    if session.auth is not None:
        playlists, error = _my_playlists(_ctx(request), session)
        if session.auth is None:  # session Deezer refusée : retour au formulaire
            session.flash = f"{LOGIN_FLASH} {error}"
        context["playlists"], context["playlists_error"] = playlists, error

    flash, session.flash = session.flash, None
    if flash:
        key = "flash_login" if flash.startswith(LOGIN_FLASH) else "flash"
        context[key] = flash.removeprefix(LOGIN_FLASH).strip()
    return render(request, "home.html", context)


def _my_playlists(
    ctx: Context, session: Session
) -> tuple[list[PlaylistInfo], str | None]:
    auth = session.auth
    if auth is None:
        return [], None
    now = time.monotonic()
    if session.playlists and now - session.playlists[0] < PLAYLISTS_CACHE_SECONDS:
        return session.playlists[1], None
    try:
        playlists = ctx.deezer.my_playlists(auth)
    except AuthenticationError as exc:
        session.auth = None
        return [], str(exc)
    except DeezerError as exc:
        return [], str(exc)
    session.playlists = (now, playlists)
    return playlists, None


# --- Exports --------------------------------------------------------------------------


@router.post("/exports")
def start_export(
    request: Request,
    playlist: PlaylistField,
    csrf: CsrfField = "",
) -> Response:
    """Lance l'export d'une playlist en tâche de fond.

    Args:
        request (Request): La requête.
        playlist (PlaylistField): L'ID ou le lien saisi.
        csrf (CsrfField): Le jeton CSRF du formulaire.

    Returns:
        Response: Redirection vers le suivi de l'export (ou l'accueil si erreur).
    """
    ctx, session = _ctx(request), _session(request)
    if not _csrf_ok(session, csrf):
        return _expired_form(request)
    try:
        playlist_id = parse_playlist_reference(playlist)
    except InvalidPlaylistReferenceError as exc:
        session.flash = str(exc)
        return _redirect("/#exporter")

    auth = session.auth

    def work(progress: Callable[[str, int, int], None]) -> PlaylistExport:
        return ctx.deezer.fetch(playlist_id, auth, progress)

    try:
        job = ctx.jobs.submit(session.owner, playlist_id, work)
    except TooManyJobsError as exc:
        session.flash = str(exc)
        return _redirect("/#exporter")
    return _redirect(f"/exports/{job.id}")


def _owned_job(request: Request, job_id: str) -> Job:
    job = _ctx(request).jobs.get(job_id, _session(request).owner)
    if job is None:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    return job


@router.get("/exports/{job_id}", response_class=HTMLResponse)
def show_export(request: Request, job_id: str) -> HTMLResponse:
    """Affiche l'avancement d'un export, puis ses résultats.

    Args:
        request (Request): La requête.
        job_id (str): L'identifiant de l'export.

    Returns:
        HTMLResponse: La page d'avancement, de résultats ou d'erreur.
    """
    job = _owned_job(request, job_id)
    if job.status == "error":
        message = job.error or "Erreur inconnue."
        return error_page(request, HTTPStatus.BAD_GATEWAY, "Export impossible", message)
    if job.status != "done" or job.result is None:
        return render(request, "progress.html", {"job": job})
    context = {
        "job": job,
        "export": job.result,
        "stats": compute_stats(job.result),
        "top_n": TOP_N,
    }
    return render(request, "result.html", context)


@router.get("/exports/{job_id}/etat")
def export_state(request: Request, job_id: str) -> JSONResponse:
    """Renvoie l'avancement d'un export (suivi en JS).

    Args:
        request (Request): La requête.
        job_id (str): L'identifiant de l'export.

    Returns:
        JSONResponse: Statut, étape et avancement.
    """
    job = _owned_job(request, job_id)
    return JSONResponse({
        "status": job.status,
        "step": job.step_label,
        "done": job.done,
        "total": job.total,
        "percent": job.percent,
    })


@router.get("/exports/{job_id}/classeur.xlsx")
def download(request: Request, job_id: str) -> Response:
    """Télécharge le classeur Excel d'un export terminé.

    Args:
        request (Request): La requête.
        job_id (str): L'identifiant de l'export.

    Returns:
        Response: Le fichier ``.xlsx``.

    Raises:
        HTTPException: 404 si l'export n'est pas (encore) disponible.
    """
    job = _owned_job(request, job_id)
    if job.result is None:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    export = job.result
    date = export.generated_at.date().isoformat()
    ascii_name = f"playlist_{export.playlist.id}_{date}.xlsx"
    utf8_name = quote(f"{export.playlist.title} {date}.xlsx")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
    return Response(
        workbook_bytes(export),
        media_type=XLSX,
        headers={"Content-Disposition": disposition},
    )


# --- Connexion ------------------------------------------------------------------------


def _sign_in(ctx: Context, session: Session, auth: DeezerAuth) -> RedirectResponse:
    session.auth = auth
    session.playlists = None
    ctx.sessions.rotate(session)  # nouvel identifiant : pas de fixation de session
    return _redirect("/#mes-playlists")


def _login_failed(session: Session, message: str) -> RedirectResponse:
    session.flash = f"{LOGIN_FLASH} {message}"
    return _redirect("/#connexion")


@router.post("/connexion/arl")
def login_arl(
    request: Request,
    arl: ArlField,
    csrf: CsrfField = "",
) -> Response:
    """Connexion par cookie « arl ».

    Args:
        request (Request): La requête.
        arl (ArlField): La valeur du cookie.
        csrf (CsrfField): Le jeton CSRF du formulaire.

    Returns:
        Response: Redirection vers les playlists (ou la connexion si erreur).

    Raises:
        HTTPException: 404 si ce mode de connexion est désactivé.
    """
    ctx, session = _ctx(request), _session(request)
    if not ctx.settings.arl_login:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    if not _csrf_ok(session, csrf):
        return _expired_form(request)
    value = arl.strip()
    if not ARL_PATTERN.fullmatch(value):
        return _login_failed(session, _BAD_ARL)
    try:
        user = ctx.deezer.login_arl(value)
    except DeezerError as exc:
        return _login_failed(session, str(exc))
    return _sign_in(ctx, session, DeezerAuth(kind="arl", secret=value, user=user))


@router.get("/connexion/deezer")
def login_oauth_start(request: Request) -> Response:
    """Redirige vers la page d'autorisation Deezer.

    Args:
        request (Request): La requête.

    Returns:
        Response: Redirection vers connect.deezer.com.

    Raises:
        HTTPException: 404 si OAuth n'est pas configuré.
    """
    ctx, session = _ctx(request), _session(request)
    if not ctx.settings.oauth_enabled:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    session.oauth_state = secrets.token_urlsafe(24)
    return _redirect(ctx.deezer.authorize_url(session.oauth_state))


@router.get("/connexion/deezer/retour")
def login_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error_reason: str = "",
) -> Response:
    """Retour de Deezer après autorisation.

    Args:
        request (Request): La requête.
        code (str): Le code d'autorisation.
        state (str): La valeur anti-CSRF renvoyée.
        error_reason (str): Motif de refus, si l'utilisateur a annulé.

    Returns:
        Response: Redirection vers les playlists (ou la connexion si erreur).

    Raises:
        HTTPException: 404 si OAuth n'est pas configuré.
    """
    ctx, session = _ctx(request), _session(request)
    if not ctx.settings.oauth_enabled:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    expected, session.oauth_state = session.oauth_state, None
    if not expected or not hmac.compare_digest(expected.encode(), state.encode()):
        return _login_failed(
            session, "Connexion expirée : recommence depuis le bouton."
        )
    if error_reason or not code:
        return _login_failed(session, "Connexion annulée sur Deezer.")
    try:
        token, expires_in, user = ctx.deezer.login_oauth(code)
    except DeezerError as exc:
        return _login_failed(session, str(exc))
    expires_at = time.monotonic() + expires_in if expires_in else None
    auth = DeezerAuth(kind="oauth", secret=token, user=user, expires_at=expires_at)
    return _sign_in(ctx, session, auth)


@router.post("/deconnexion")
def logout(request: Request, csrf: CsrfField = "") -> Response:
    """Déconnexion : oublie l'accès Deezer et change d'identifiant de session.

    Args:
        request (Request): La requête.
        csrf (CsrfField): Le jeton CSRF du formulaire.

    Returns:
        Response: Redirection vers l'accueil.
    """
    session = _session(request)
    if not _csrf_ok(session, csrf):
        return _expired_form(request)
    session.auth = None
    session.playlists = None
    _ctx(request).sessions.rotate(session)
    return _redirect("/")


# --- Assemblage -----------------------------------------------------------------------


def _templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters["duration"] = format_duration
    env.filters["track"] = describe_track
    env.filters["clock"] = lambda s: f"{s // 60}:{s % 60:02d}"
    env.filters["percent"] = lambda x: f"{100 * x:.1f} %".replace(".", ",")
    env.globals.update(version=__version__, settings=settings)
    return templates


def _security_headers(response: Response, path: str, csp: str, *, https: bool) -> None:
    headers = response.headers
    headers["Content-Security-Policy"] = csp
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "same-origin"
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if https:
        headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if path.startswith("/static/"):
        headers["Cache-Control"] = "public, max-age=86400"
    else:  # pages liées à une session : jamais en cache partagé
        headers["Cache-Control"] = "no-store"


def create_app(
    settings: Settings | None = None,
    *,
    backend: Backend | None = None,
    jobs: JobManager | None = None,
) -> FastAPI:
    """Construit l'application.

    Args:
        settings (Settings | None): Réglages (défaut : lus dans l'environnement).
        backend (Backend | None): Accès à Deezer (remplaçable en test).
        jobs (JobManager | None): File des exports (idem).

    Returns:
        FastAPI: L'application ASGI.
    """
    cfg = settings or Settings.from_env()
    ctx = Context(
        settings=cfg,
        deezer=backend or DeezerBackend(cfg),
        jobs=jobs or JobManager(workers=cfg.workers, max_pending=cfg.max_pending_jobs),
        sessions=SessionStore(ttl_seconds=cfg.session_ttl_minutes * 60),
        templates=_templates(cfg),
    )
    csp = content_security_policy(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            ctx.jobs.shutdown()

    app = FastAPI(
        title="Deezer Export",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def session_and_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        session = None
        if not request.url.path.startswith(_NO_SESSION_PREFIXES):
            session = ctx.sessions.load(request.cookies.get(COOKIE_NAME))
            request.state.session = session

        response = await call_next(request)

        if session is not None and session.is_new:
            response.set_cookie(
                COOKIE_NAME,
                session.id,
                httponly=True,
                secure=cfg.secure_cookies,
                samesite="lax",
                path="/",
            )
        _security_headers(response, request.url.path, csp, https=cfg.secure_cookies)
        return response

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            message = "Cette page n'existe pas ou a expiré."
            return error_page(request, exc.status_code, "Introuvable", message)
        return error_page(request, exc.status_code, "Erreur", str(exc.detail))

    return app
