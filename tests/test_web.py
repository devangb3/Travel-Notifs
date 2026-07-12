from pathlib import Path

import httpx
import pytest

from travel_notifs.config import Settings
from travel_notifs.web import create_app


def app_for(tmp_path: Path):
    app = create_app(
        Settings(
            database_path=tmp_path / "web.db",
            app_env="development",
            google_maps_api_key="",
        )
    )
    return app


async def test_dashboard_renders(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(app=app_for(tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
        response = await web.get("/")
    assert response.status_code == 200
    assert "Know the minute" in response.text
    assert "DART" in response.text
    assert "Unitrans" in response.text
    assert "Yolobus" in response.text


async def test_demo_planner_returns_itineraries(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(app=app_for(tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
        response = await web.post(
            "/api/plan",
            json={
                "origin": "UC Davis Memorial Union",
                "destination": "Davis Amtrak",
                "travel_at": "2026-07-11T08:00:00-07:00",
                "timing_mode": "depart",
                "agency_id": "unitrans",
            },
        )
    assert response.status_code == 200
    assert len(response.json()["itineraries"]) == 3


async def test_dashboard_displays_trip_in_agency_timezone(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(app=app_for(tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
        created = await web.post(
            "/api/trips",
            json={
                "origin": "Davis",
                "destination": "Sacramento Airport",
                "travel_at": "2026-07-12T04:30:00+00:00",
                "timing_mode": "depart",
                "agency_id": "yolobus",
                "recurrence": "once",
                "itinerary_id": "42b",
            },
        )
        response = await web.get("/")

    assert created.status_code == 201
    assert "2026-07-11 21:30 PDT" in response.text


async def test_invitation_creates_production_session(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "production.db",
            app_env="production",
            app_secret="test-secret",
            admin_token="admin-secret",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://test", follow_redirects=True
    ) as web:
        invite = await web.post("/admin/invitations", headers={"x-admin-token": "admin-secret"})
        assert invite.status_code == 200
        token = invite.json()["url"].rsplit("/", 1)[-1]
        joined = await web.post(f"/join/{token}", data={"display_name": "Ada"})
    assert joined.status_code == 200
    assert "Know the minute" in joined.text


async def test_admin_endpoint_is_protected(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "protected.db"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
        assert (await web.post("/admin/invitations")).status_code == 403


async def test_creates_telegram_deep_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def username(_notifier: object) -> str:
        return "arrival_bot"

    monkeypatch.setattr("travel_notifs.web.TelegramNotifier.username", username)
    app = create_app(
        Settings(
            database_path=tmp_path / "telegram.db",
            app_env="development",
            telegram_bot_token="secret",
            google_maps_api_key="",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
        response = await web.post("/api/telegram/pairing")
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://t.me/arrival_bot?start=")
