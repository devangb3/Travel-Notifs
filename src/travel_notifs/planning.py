from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

import httpx

from travel_notifs.agencies import AGENCIES, AgencyId


class PlanningError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlaceSuggestion:
    label: str
    place_id: str


@dataclass(frozen=True, slots=True)
class Leg:
    mode: str
    from_name: str
    to_name: str
    start_time: str
    end_time: str
    route: str = ""
    headsign: str = ""
    agency: str = ""
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class Itinerary:
    id: str
    agency_id: AgencyId
    start_time: str
    end_time: str
    duration_minutes: int
    transfers: int
    legs: tuple[Leg, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TransitProvider(Protocol):
    async def plan(
        self,
        agency_id: AgencyId,
        origin_place_id: str,
        destination_place_id: str,
        travel_at: datetime,
        arrive_by: bool,
    ) -> list[Itinerary]: ...


class GoogleTransitProvider:
    routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    places_url = "https://places.googleapis.com/v1/places:autocomplete"
    route_fields = ",".join(
        (
            "routes.duration",
            "routes.legs.steps.travelMode",
            "routes.legs.steps.staticDuration",
            "routes.legs.steps.transitDetails.stopDetails",
            "routes.legs.steps.transitDetails.transitLine",
            "routes.legs.steps.transitDetails.headsign",
        )
    )

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=20)

    async def autocomplete(
        self, query: str, agency_id: AgencyId, session_token: str
    ) -> list[PlaceSuggestion]:
        agency = AGENCIES[agency_id]
        center_lat = (agency.bounds.south + agency.bounds.north) / 2
        center_lon = (agency.bounds.west + agency.bounds.east) / 2
        response = await self.client.post(
            self.places_url,
            headers={"X-Goog-Api-Key": self.api_key},
            json={
                "input": query,
                "sessionToken": session_token,
                "includedRegionCodes": ["us"],
                "locationBias": {
                    "circle": {
                        "center": {"latitude": center_lat, "longitude": center_lon},
                        "radius": 50000,
                    }
                },
            },
        )
        response.raise_for_status()
        suggestions = []
        for item in response.json().get("suggestions", []):
            prediction = item.get("placePrediction", {})
            if prediction.get("placeId") and prediction.get("text", {}).get("text"):
                suggestions.append(
                    PlaceSuggestion(prediction["text"]["text"], prediction["placeId"])
                )
        return suggestions[:6]

    async def plan(
        self,
        agency_id: AgencyId,
        origin_place_id: str,
        destination_place_id: str,
        travel_at: datetime,
        arrive_by: bool,
    ) -> list[Itinerary]:
        timing = {"arrivalTime" if arrive_by else "departureTime": travel_at.isoformat()}
        response = await self.client.post(
            self.routes_url,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": self.route_fields,
            },
            json={
                "origin": {"placeId": origin_place_id},
                "destination": {"placeId": destination_place_id},
                "travelMode": "TRANSIT",
                "computeAlternativeRoutes": True,
                "languageCode": "en-US",
                **timing,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlanningError("Google could not plan this transit trip") from exc
        return normalize_routes(response.json(), agency_id)


class DemoPlanner:
    async def plan(
        self,
        agency_id: AgencyId,
        origin: str,
        destination: str,
        travel_at: datetime,
    ) -> list[Itinerary]:
        route = "239" if agency_id == AgencyId.DART else "V"
        agency = AGENCIES[agency_id]
        results = []
        for index, offset in enumerate((0, 8, 17), start=1):
            start = travel_at + timedelta(minutes=offset)
            end = start + timedelta(minutes=34 + index * 3)
            fingerprint = make_fingerprint(
                agency.name, route, destination, f"Nearby {agency.short_name} stop", destination
            )
            legs = (
                Leg("WALK", origin, f"Nearby {agency.short_name} stop", start.isoformat(), ""),
                Leg(
                    "BUS",
                    f"Nearby {agency.short_name} stop",
                    destination,
                    (start + timedelta(minutes=7)).isoformat(),
                    end.isoformat(),
                    route,
                    destination,
                    agency.name,
                    fingerprint,
                ),
            )
            results.append(
                Itinerary(
                    f"{agency_id}-{index}",
                    agency_id,
                    start.isoformat(),
                    end.isoformat(),
                    round((end - start).total_seconds() / 60),
                    0,
                    legs,
                )
            )
        return results


def normalize_routes(payload: dict[str, object], agency_id: AgencyId) -> list[Itinerary]:
    itineraries = []
    expected_names = {name.casefold() for name in AGENCIES[agency_id].google_names}
    for route_index, route in enumerate(payload.get("routes", []), start=1):
        raw_steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
        transit_legs = []
        for step in raw_steps:
            details = step.get("transitDetails")
            if not details:
                continue
            line = details.get("transitLine", {})
            agency_names = [item.get("name", "") for item in line.get("agencies", [])]
            if agency_names and not _agency_matches(agency_names, expected_names):
                continue
            stops = details.get("stopDetails", {})
            departure = stops.get("departureTime", "")
            arrival = stops.get("arrivalTime", "")
            from_name = stops.get("departureStop", {}).get("name", "Boarding stop")
            to_name = stops.get("arrivalStop", {}).get("name", "Arrival stop")
            route_name = line.get("nameShort") or line.get("name") or "Transit"
            headsign = details.get("headsign", "")
            agency_name = agency_names[0] if agency_names else AGENCIES[agency_id].name
            transit_legs.append(
                Leg(
                    step.get("travelMode", "TRANSIT"),
                    from_name,
                    to_name,
                    departure,
                    arrival,
                    route_name,
                    headsign,
                    agency_name,
                    make_fingerprint(agency_name, route_name, headsign, from_name, to_name),
                )
            )
        if not transit_legs:
            continue
        duration = _seconds(route.get("duration", "0s"))
        identity = "|".join(leg.fingerprint for leg in transit_legs)
        itineraries.append(
            Itinerary(
                sha256(identity.encode()).hexdigest()[:16] or f"route-{route_index}",
                agency_id,
                transit_legs[0].start_time,
                transit_legs[-1].end_time,
                round(duration / 60),
                max(0, len(transit_legs) - 1),
                tuple(transit_legs),
            )
        )
    return itineraries


def make_fingerprint(agency: str, route: str, headsign: str, origin: str, destination: str) -> str:
    canonical = "|".join(
        value.strip().casefold() for value in (agency, route, headsign, origin, destination)
    )
    return sha256(canonical.encode()).hexdigest()[:20]


def _seconds(value: str) -> int:
    try:
        return round(float(value.removesuffix("s")))
    except (TypeError, ValueError):
        return 0


def _agency_matches(names: list[str], expected: set[str]) -> bool:
    return any(
        candidate in known or known in candidate
        for name in names
        for candidate in (name.casefold(),)
        for known in expected
    )
