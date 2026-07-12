from datetime import UTC, datetime, timedelta

from travel_notifs.agencies import AgencyId
from travel_notifs.alerts import AlertEngine
from travel_notifs.domain import AlertState, PredictionStatus, StopPrediction

NOW = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)


def prediction(
    minutes_until: int = 10,
    delay_minutes: int = 0,
    *,
    status: PredictionStatus = PredictionStatus.LIVE,
    feed_age_seconds: int = 10,
) -> StopPrediction:
    scheduled = NOW + timedelta(minutes=minutes_until - delay_minutes)
    predicted = NOW + timedelta(minutes=minutes_until)
    return StopPrediction(
        agency_id=AgencyId.DART,
        trip_id="trip-1",
        stop_id="stop-1",
        route_label="239",
        headsign="Downtown",
        stop_name="Parker Road Station",
        scheduled_arrival=scheduled,
        predicted_arrival=predicted,
        feed_timestamp=NOW - timedelta(seconds=feed_age_seconds),
        status=status,
    )


def test_combines_crossed_milestones() -> None:
    state = AlertState()
    result = AlertEngine().evaluate(prediction(minutes_until=9), state, NOW)
    assert result.should_send
    assert result.reasons == ("15-minute-milestone", "10-minute-milestone")


def test_milestone_is_sent_once() -> None:
    state = AlertState()
    engine = AlertEngine()
    assert engine.evaluate(prediction(), state, NOW).should_send
    assert not engine.evaluate(prediction(), state, NOW + timedelta(seconds=30)).should_send


def test_eta_change_triggers_after_threshold() -> None:
    state = AlertState(last_communicated_eta=NOW + timedelta(minutes=10))
    result = AlertEngine(cooldown_seconds=0).evaluate(prediction(minutes_until=13), state, NOW)
    assert result.should_send
    assert "eta-changed" in result.reasons


def test_small_eta_change_does_not_trigger() -> None:
    state = AlertState(
        sent_milestones={15, 10},
        last_communicated_eta=NOW + timedelta(minutes=10),
    )
    result = AlertEngine(cooldown_seconds=0).evaluate(prediction(minutes_until=11), state, NOW)
    assert not result.should_send


def test_stale_feed_is_not_reported_as_delay() -> None:
    state = AlertState()
    result = AlertEngine().evaluate(prediction(feed_age_seconds=300), state, NOW)
    assert result.should_send
    assert result.reasons == ("live-data-unavailable",)
    assert "temporarily unavailable" in result.message


def test_cancellation_is_sent_once() -> None:
    state = AlertState()
    engine = AlertEngine()
    cancelled = prediction(status=PredictionStatus.CANCELLED)
    assert engine.evaluate(cancelled, state, NOW).should_send
    assert not engine.evaluate(cancelled, state, NOW).should_send


def test_message_never_tells_user_when_to_leave() -> None:
    result = AlertEngine().evaluate(prediction(), AlertState(), NOW)
    assert "leave" not in result.message.lower()


def test_message_uses_agency_local_timezone() -> None:
    result = AlertEngine().evaluate(prediction(), AlertState(), NOW)
    assert "10:10 AM CDT" in result.message
    assert "3:10 PM" not in result.message
