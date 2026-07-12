from pathlib import Path

from travel_notifs.domain import AlertState
from travel_notifs.storage import Database


def test_invitation_can_only_be_consumed_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    token = database.create_invitation()
    assert database.consume_invitation(token, "Ada") is not None
    assert database.consume_invitation(token, "Grace") is None


def test_create_and_pause_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    user_id = database.get_or_create_development_user()
    trip_id = database.create_trip(
        user_id,
        {
            "agency_id": "dart",
            "origin_label": "Home",
            "destination_label": "Office",
            "timing_mode": "depart",
            "travel_at": "2026-07-11T08:00:00-05:00",
        },
    )
    assert database.set_trip_status(user_id, trip_id, "paused")
    assert database.list_trips(user_id)[0].status == "paused"


def test_lists_monitorable_trip_and_deduplicates_delivery(tmp_path: Path) -> None:
    database = Database(tmp_path / "monitor.db")
    database.initialize()
    user_id = database.get_or_create_development_user()
    trip_id = database.create_trip(
        user_id,
        {
            "agency_id": "unitrans",
            "origin_label": "Memorial Union",
            "origin_place_id": "origin-place",
            "destination_label": "Davis Amtrak",
            "destination_place_id": "destination-place",
            "timing_mode": "depart",
            "travel_at": "2026-07-13T08:00:00-07:00",
            "selected_itinerary": {
                "legs": [
                    {
                        "start_time": "2026-07-13T08:10:00-07:00",
                        "fingerprint": "leg-one",
                    }
                ]
            },
        },
    )
    monitored = database.list_active_monitored_trips()
    assert monitored[0].selected_itinerary["legs"][0]["fingerprint"] == "leg-one"
    assert database.record_delivery(trip_id, "telegram", "123", "delivery-one")
    assert not database.record_delivery(trip_id, "telegram", "123", "delivery-one")
    assert database.delivery_exists("delivery-one")


def test_telegram_pairing_token_is_single_use(tmp_path: Path) -> None:
    database = Database(tmp_path / "pairing.db")
    database.initialize()
    user_id = database.get_or_create_development_user()
    token = database.create_telegram_pairing(user_id)
    assert database.consume_telegram_pairing(token, "12345")
    assert database.telegram_is_paired(user_id)
    assert not database.consume_telegram_pairing(token, "99999")


def test_alert_state_survives_worker_restart(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.initialize()
    user_id = database.get_or_create_development_user()
    trip_id = database.create_trip(
        user_id,
        {
            "agency_id": "dart",
            "origin_label": "Parker",
            "destination_label": "Plano",
            "timing_mode": "depart",
            "travel_at": "2026-07-13T08:00:00-05:00",
        },
    )
    database.save_alert_state(
        trip_id, "leg-one", AlertState(sent_milestones={15, 10}, missing_notified=True)
    )
    restored = database.load_alert_state(trip_id, "leg-one")
    assert restored.sent_milestones == {15, 10}
    assert restored.missing_notified
