from datetime import UTC, datetime

from travel_notifs.planning import Itinerary, Leg
from travel_notifs.storage import MonitoredTrip
from travel_notifs.worker import due_request, match_leg


def monitored_trip(**overrides: object) -> MonitoredTrip:
    values = {
        "id": 1,
        "user_id": 1,
        "agency_id": "dart",
        "origin_place_id": "origin",
        "destination_place_id": "destination",
        "timing_mode": "depart",
        "travel_at": "2026-07-13T14:00:00+00:00",
        "recurrence": "once",
        "weekdays": "",
        "selected_itinerary": {
            "legs": [
                {
                    "start_time": "2026-07-13T14:10:00+00:00",
                    "fingerprint": "selected-leg",
                }
            ]
        },
        "email": None,
        "telegram_chat_id": None,
    }
    values.update(overrides)
    return MonitoredTrip(**values)


def test_once_trip_is_due_only_in_monitoring_window() -> None:
    trip = monitored_trip()
    assert due_request(trip, datetime(2026, 7, 13, 13, 55, tzinfo=UTC)) == datetime(
        2026, 7, 13, 14, 0, tzinfo=UTC
    )
    assert due_request(trip, datetime(2026, 7, 13, 13, 40, tzinfo=UTC)) is None


def test_weekly_trip_moves_request_to_matching_weekday() -> None:
    trip = monitored_trip(recurrence="weekly", weekdays="0")
    request = due_request(trip, datetime(2026, 7, 20, 13, 55, tzinfo=UTC))
    assert request == datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


def test_leg_matching_uses_fingerprint_and_closest_time() -> None:
    legs = (
        Leg(
            "TRANSIT",
            "Parker",
            "Plano",
            "2026-07-13T14:13:00+00:00",
            "2026-07-13T14:35:00+00:00",
            "239",
            "Downtown",
            "DART",
            "selected-leg",
        ),
    )
    itinerary = Itinerary("one", "dart", legs[0].start_time, legs[0].end_time, 22, 0, legs)
    matched = match_leg("selected-leg", datetime(2026, 7, 13, 14, 10, tzinfo=UTC), [itinerary])
    assert matched is not None
    assert matched.route == "239"
