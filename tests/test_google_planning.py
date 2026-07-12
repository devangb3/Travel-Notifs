import json
from datetime import UTC, datetime

import httpx

from travel_notifs.agencies import AgencyId
from travel_notifs.planning import GoogleTransitProvider, normalize_routes


def dart_response() -> dict[str, object]:
    return {
        "routes": [
            {
                "duration": "1800s",
                "legs": [
                    {
                        "steps": [
                            {"travelMode": "WALK", "staticDuration": "300s"},
                            {
                                "travelMode": "TRANSIT",
                                "transitDetails": {
                                    "stopDetails": {
                                        "departureStop": {"name": "Parker Road Station"},
                                        "departureTime": "2026-07-13T13:14:00Z",
                                        "arrivalStop": {"name": "Downtown Plano"},
                                        "arrivalTime": "2026-07-13T13:35:00Z",
                                    },
                                    "transitLine": {
                                        "agencies": [{"name": "Dallas Area Rapid Transit (DART)"}],
                                        "nameShort": "239",
                                        "name": "Northwest Plano",
                                    },
                                    "headsign": "Downtown",
                                },
                            },
                        ]
                    }
                ],
            }
        ]
    }


def test_normalizes_google_transit_leg() -> None:
    itineraries = normalize_routes(dart_response(), AgencyId.DART)
    assert len(itineraries) == 1
    assert itineraries[0].duration_minutes == 30
    assert itineraries[0].legs[0].route == "239"
    assert itineraries[0].legs[0].from_name == "Parker Road Station"
    assert itineraries[0].legs[0].fingerprint


def test_normalizes_yolobus_operator_and_route() -> None:
    payload = dart_response()
    details = payload["routes"][0]["legs"][0]["steps"][1]["transitDetails"]
    details["transitLine"]["agencies"] = [{"name": "Yolo County Transportation District"}]
    details["transitLine"]["nameShort"] = "42B"

    itineraries = normalize_routes(payload, AgencyId.YOLOBUS)

    assert len(itineraries) == 1
    assert itineraries[0].legs[0].route == "42B"
    assert itineraries[0].legs[0].agency == "Yolo County Transportation District"


async def test_google_provider_sends_transit_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "secret"
        assert json.loads(request.content)["travelMode"] == "TRANSIT"
        return httpx.Response(200, json=dart_response())

    provider = GoogleTransitProvider(
        "secret", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    itineraries = await provider.plan(
        AgencyId.DART,
        "origin-place",
        "destination-place",
        datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
        False,
    )
    assert itineraries[0].legs[0].headsign == "Downtown"


async def test_google_autocomplete_is_region_biased() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["includedRegionCodes"] == ["us"]
        assert body["locationBias"]["circle"]["center"]["latitude"] > 32
        return httpx.Response(
            200,
            json={
                "suggestions": [
                    {
                        "placePrediction": {
                            "placeId": "place-1",
                            "text": {"text": "Parker Road Station, Plano, TX"},
                        }
                    }
                ]
            },
        )

    provider = GoogleTransitProvider(
        "secret", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    suggestions = await provider.autocomplete("Parker", AgencyId.DART, "session")
    assert suggestions[0].place_id == "place-1"
