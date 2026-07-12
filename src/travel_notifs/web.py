import hashlib
import hmac
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from travel_notifs.agencies import AGENCIES, AgencyId, agency_local_time
from travel_notifs.config import Settings, get_settings
from travel_notifs.notifications import NotificationError, TelegramNotifier
from travel_notifs.planning import DemoPlanner, GoogleTransitProvider, PlanningError
from travel_notifs.storage import Database

PACKAGE_ROOT = Path(__file__).parent


class PlanRequest(BaseModel):
    origin: str = Field(min_length=3, max_length=200)
    destination: str = Field(min_length=3, max_length=200)
    travel_at: datetime
    timing_mode: str = Field(pattern="^(depart|arrive)$")
    agency_id: AgencyId
    origin_place_id: str = ""
    destination_place_id: str = ""


class SaveTripRequest(PlanRequest):
    recurrence: str = Field(pattern="^(once|weekly)$")
    weekdays: list[str] = Field(default_factory=list)
    itinerary_id: str
    selected_itinerary: dict[str, object] = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings.database_path)
    database.initialize()
    demo_planner = DemoPlanner()
    google = (
        GoogleTransitProvider(settings.google_maps_api_key)
        if settings.google_maps_api_key
        else None
    )
    telegram = (
        TelegramNotifier(settings.telegram_bot_token) if settings.telegram_bot_token else None
    )

    app = FastAPI(title="Transit Dispatch", version="0.1.0")
    app.state.settings = settings
    app.state.database = database
    app.state.planner = google or demo_planner
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")

    def format_trip_time(value: str, agency_id: str) -> str:
        local = agency_local_time(datetime.fromisoformat(value), agency_id)
        return local.strftime("%Y-%m-%d %H:%M %Z")

    templates.env.filters["trip_time"] = format_trip_time

    def sign_user(user_id: int) -> str:
        expires = int(time.time()) + 30 * 24 * 60 * 60
        payload = f"{user_id}.{expires}"
        signature = hmac.new(
            settings.app_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    def current_user_id(request: Request) -> int:
        cookie = request.cookies.get("travel_session", "")
        try:
            raw_user_id, raw_expires, signature = cookie.split(".", 2)
            payload = f"{raw_user_id}.{raw_expires}"
            expected = hmac.new(
                settings.app_secret.encode(), payload.encode(), hashlib.sha256
            ).hexdigest()
            user_id = int(raw_user_id)
            if (
                hmac.compare_digest(signature, expected)
                and int(raw_expires) > time.time()
                and database.user_exists(user_id)
            ):
                return user_id
        except (ValueError, TypeError):
            pass
        if settings.app_env == "development":
            return database.get_or_create_development_user()
        raise HTTPException(status_code=401, detail="Invitation sign-in required")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "demo_mode": settings.demo_mode,
            "agencies": [agency.id for agency in AGENCIES.values()],
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        user_id = current_user_id(request)
        now = datetime.now(tz=UTC)
        trip_counts = database.dashboard_trip_counts(user_id, now)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "trips": database.list_dashboard_trips(user_id, "monitored", now),
                "trip_counts": trip_counts,
                "demo_mode": settings.demo_mode,
                "agencies": AGENCIES.values(),
                "telegram_available": telegram is not None,
                "telegram_paired": database.telegram_is_paired(user_id),
            },
        )

    @app.post("/api/telegram/pairing")
    async def create_telegram_pairing(request: Request) -> dict[str, str]:
        if telegram is None:
            raise HTTPException(status_code=503, detail="Telegram bot is not configured")
        user_id = current_user_id(request)
        try:
            username = await telegram.username()
        except NotificationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        token = database.create_telegram_pairing(user_id)
        return {"url": f"https://t.me/{username}?start={token}"}

    @app.post("/api/plan")
    async def plan_trip(payload: PlanRequest) -> JSONResponse:
        if google:
            if not payload.origin_place_id or not payload.destination_place_id:
                raise HTTPException(
                    status_code=422, detail="Select both addresses from suggestions"
                )
            try:
                itineraries = await google.plan(
                    payload.agency_id,
                    payload.origin_place_id,
                    payload.destination_place_id,
                    payload.travel_at,
                    payload.timing_mode == "arrive",
                )
            except PlanningError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            itineraries = await demo_planner.plan(
                payload.agency_id,
                payload.origin,
                payload.destination,
                payload.travel_at,
            )
        return JSONResponse({"itineraries": [item.to_dict() for item in itineraries]})

    @app.get("/api/places/autocomplete")
    async def autocomplete(q: str, agency_id: AgencyId, session_token: str) -> dict[str, object]:
        if not google:
            return {"suggestions": []}
        if len(q.strip()) < 3:
            return {"suggestions": []}
        suggestions = await google.autocomplete(q.strip(), agency_id, session_token)
        return {"suggestions": [asdict(item) for item in suggestions]}

    @app.get("/api/trips/{view}", response_class=HTMLResponse)
    async def trip_rows(
        request: Request, view: Literal["paused", "past"]
    ) -> HTMLResponse:
        trips = database.list_dashboard_trips(current_user_id(request), view)
        return templates.TemplateResponse(
            request,
            "_trip_rows.html",
            {"trips": trips, "trip_view": view},
        )

    @app.post("/api/trips", status_code=201)
    async def save_trip(request: Request, payload: SaveTripRequest) -> dict[str, int]:
        user_id = current_user_id(request)
        trip_id = database.create_trip(
            user_id,
            {
                "agency_id": payload.agency_id,
                "origin_label": payload.origin,
                "origin_place_id": payload.origin_place_id,
                "destination_label": payload.destination,
                "destination_place_id": payload.destination_place_id,
                "timing_mode": payload.timing_mode,
                "travel_at": payload.travel_at.isoformat(),
                "recurrence": payload.recurrence,
                "weekdays": ",".join(payload.weekdays),
                "selected_itinerary": payload.selected_itinerary,
            },
        )
        return {"id": trip_id}

    @app.post("/api/trips/{trip_id}/status")
    async def update_trip_status(
        request: Request, trip_id: int, status: str = Form()
    ) -> RedirectResponse:
        if status not in {"active", "paused"}:
            raise HTTPException(status_code=422, detail="Unsupported status")
        updated = database.set_trip_status(current_user_id(request), trip_id, status)
        if not updated:
            raise HTTPException(status_code=404, detail="Trip not found")
        return RedirectResponse(url="/", status_code=303)

    @app.post("/admin/invitations")
    async def create_invitation(request: Request) -> dict[str, str]:
        if request.headers.get("x-admin-token") != settings.admin_token:
            raise HTTPException(status_code=403, detail="Admin token required")
        token = database.create_invitation()
        return {"url": f"{settings.base_url}/join/{token}"}

    @app.get("/join/{token}", response_class=HTMLResponse)
    async def join_page(request: Request, token: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "join.html", {"token": token})

    @app.post("/join/{token}")
    async def join(
        token: str, display_name: str = Form(min_length=2, max_length=80)
    ) -> RedirectResponse:
        user_id = database.consume_invitation(token, display_name)
        if user_id is None:
            raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            "travel_session",
            sign_user(user_id),
            httponly=True,
            secure=settings.app_env == "production",
            samesite="lax",
            max_age=30 * 24 * 60 * 60,
        )
        return response

    return app


app = create_app()
