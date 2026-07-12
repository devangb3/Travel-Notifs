import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from travel_notifs.agencies import AgencyId
from travel_notifs.config import get_settings
from travel_notifs.planning import GoogleTransitProvider, PlanningError


async def main() -> None:
    settings = get_settings()
    if not settings.routes_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY or GOOGLE_ROUTES_API_KEY is not configured")

    provider = GoogleTransitProvider(settings.routes_key)
    try:
        origins = await provider.autocomplete("Parker Road Station", AgencyId.DART, "smoke")
        destinations = await provider.autocomplete("Downtown Plano Station", AgencyId.DART, "smoke")
        print(f"places: origin={len(origins)} destination={len(destinations)}")
        if not origins or not destinations:
            raise SystemExit("Google Places returned no smoke-test locations")
        itineraries = await provider.plan(
            AgencyId.DART,
            origins[0].place_id,
            destinations[0].place_id,
            datetime.now(tz=UTC) + timedelta(hours=1),
            False,
        )
        print(f"routes: itineraries={len(itineraries)}")
    except PlanningError as exc:
        if isinstance(exc.__cause__, httpx.HTTPStatusError):
            response = exc.__cause__.response
            print(f"google error: status={response.status_code} body={response.text[:500]}")
        else:
            print(f"google error: {exc}")
        raise SystemExit(1) from exc
    except httpx.HTTPStatusError as exc:
        print(f"google error: status={exc.response.status_code} body={exc.response.text[:500]}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
