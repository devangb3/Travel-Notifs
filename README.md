# Transit Dispatch

An invite-only arrival notification service for DART, Unitrans, and Yolobus. Users plan
one-time or recurring trips, choose an itinerary, and receive the vehicle's
predicted arrival time at each boarding stop.

The product deliberately does **not** calculate when a rider should leave.

## Current status

The repository contains the first runnable vertical slice:

- DART, Unitrans, and Yolobus agency registry.
- Google Places and Routes provider shared by every launch agency.
- Milestone, ETA-change, cancellation, stale-feed, and recovery alert logic.
- Telegram, Resend email, and dry-run notification providers.
- SQLite persistence, single-use invitations, and signed sessions.
- Responsive trip planner and monitored-trip dashboard.
- Demo itineraries that work without API keys.
- Lightweight Caddy/FastAPI/worker Docker deployment.

Live route planning is enabled when the Google key can access both Places API
and Routes API. Friends connect Telegram from the dashboard using a ten-minute,
single-use bot link. Notification delivery remains dry-run until `DRY_RUN=false`.

## Local setup

Python commands must run inside the project virtual environment:

```bash
uv venv
source .venv/bin/activate
uv sync
cp .env.example .env
pytest -q
uvicorn travel_notifs.web:app --reload
```

The web application starts in demo mode when `GOOGLE_MAPS_API_KEY` is empty.
Demo mode returns representative DART, Unitrans, and Yolobus itineraries and never sends
external notifications.

## Deployment

Google handles transit routing, so the VPS runs only Caddy, FastAPI, the worker,
and SQLite. A 1 GB instance may work; 2 GB provides safer headroom. The existing
4 GB development target is more than sufficient.

## Credentials

No credentials are needed for the local demo. Live integration requires:

1. A server-side Google Maps Platform key with Places API (New) and Routes API
   enabled, plus strict API and quota restrictions.
2. A Telegram bot token from BotFather.
3. Optionally, a Resend API key and verified sending domain for email.

Copy `.env.example` to `.env` and add credentials there. Never commit `.env`.

## Administration

Create an invitation with the configured `ADMIN_TOKEN`:

```bash
curl -X POST http://localhost:8000/admin/invitations \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

The detailed design is in
[`docs/plans/2026-07-11-google-routes-provider-design.md`](docs/plans/2026-07-11-google-routes-provider-design.md).
