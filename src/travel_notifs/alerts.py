from datetime import datetime
from hashlib import sha256

from travel_notifs.domain import (
    AlertDecision,
    AlertState,
    PredictionStatus,
    StopPrediction,
)


class AlertEngine:
    def __init__(
        self,
        milestones: tuple[int, ...] = (15, 10, 5),
        eta_change_minutes: int = 2,
        cooldown_seconds: int = 120,
        stale_after_seconds: int = 180,
    ) -> None:
        self.milestones = tuple(sorted(milestones, reverse=True))
        self.eta_change_minutes = eta_change_minutes
        self.cooldown_seconds = cooldown_seconds
        self.stale_after_seconds = stale_after_seconds

    def evaluate(
        self,
        prediction: StopPrediction,
        state: AlertState,
        now: datetime,
    ) -> AlertDecision:
        if prediction.status == PredictionStatus.CANCELLED:
            if state.cancellation_notified:
                return AlertDecision(False)
            state.cancellation_notified = True
            return self._decision(prediction, ("cancelled",), now)

        if prediction.is_stale(now, self.stale_after_seconds) or (
            prediction.status == PredictionStatus.SCHEDULED_ONLY
        ):
            if state.missing_notified:
                return AlertDecision(False)
            state.missing_notified = True
            return self._decision(prediction, ("live-data-unavailable",), now)

        reasons: list[str] = []
        minutes_until = (prediction.effective_arrival - now).total_seconds() / 60
        for milestone in self.milestones:
            crossed_milestone = 0 < minutes_until <= milestone
            if crossed_milestone and milestone not in state.sent_milestones:
                state.sent_milestones.add(milestone)
                reasons.append(f"{milestone}-minute-milestone")

        if state.last_communicated_eta is not None:
            change = abs(prediction.effective_arrival - state.last_communicated_eta)
            if change.total_seconds() >= self.eta_change_minutes * 60:
                reasons.append("eta-changed")

        recovered = state.missing_notified
        if recovered:
            reasons.append("live-data-restored")
            state.missing_notified = False

        if not reasons:
            return AlertDecision(False)

        if state.last_sent_at and not recovered:
            since_last = (now - state.last_sent_at).total_seconds()
            milestone_only = all(reason.endswith("milestone") for reason in reasons)
            if since_last < self.cooldown_seconds and not milestone_only:
                return AlertDecision(False)

        state.last_communicated_eta = prediction.effective_arrival
        state.last_sent_at = now
        return self._decision(prediction, tuple(reasons), now)

    def _decision(
        self,
        prediction: StopPrediction,
        reasons: tuple[str, ...],
        now: datetime,
    ) -> AlertDecision:
        message = format_prediction(prediction, reasons)
        raw_key = ":".join(
            (
                prediction.agency_id,
                prediction.trip_id,
                prediction.stop_id,
                prediction.effective_arrival.isoformat(),
                ",".join(reasons),
                now.strftime("%Y-%m-%d"),
            )
        )
        return AlertDecision(
            should_send=True,
            reasons=reasons,
            message=message,
            idempotency_key=sha256(raw_key.encode()).hexdigest(),
        )


def format_prediction(prediction: StopPrediction, reasons: tuple[str, ...]) -> str:
    agency = prediction.agency_id.upper()
    scheduled = (
        prediction.scheduled_arrival.strftime("%-I:%M %p")
        if prediction.scheduled_arrival
        else "unavailable"
    )
    updated = prediction.feed_timestamp.strftime("%-I:%M %p")

    if "cancelled" in reasons:
        return (
            f"{agency} · Route {prediction.route_label} to {prediction.headsign}\n"
            f"This trip is cancelled at {prediction.stop_name}.\n"
            f"Scheduled arrival: {scheduled}"
        )

    if "live-data-unavailable" in reasons:
        schedule_line = (
            f"Scheduled arrival at {prediction.stop_name}: {scheduled}.\n"
            if prediction.scheduled_arrival
            else ""
        )
        return (
            "Live prediction is temporarily unavailable.\n"
            f"{schedule_line}"
            f"Last live update: {updated}."
        )

    expected = prediction.effective_arrival.strftime("%-I:%M %p")
    delay = prediction.delay_minutes
    status = ""
    if delay is not None:
        status = " · on time"
        if delay > 0:
            status = f" · {delay} minutes late"
        elif delay < 0:
            status = f" · {abs(delay)} minutes early"

    schedule_line = f"Scheduled: {scheduled}{status}\n" if prediction.scheduled_arrival else ""
    return (
        f"{agency} · Route {prediction.route_label} to {prediction.headsign}\n"
        f"Expected at {prediction.stop_name}: {expected}\n"
        f"{schedule_line}"
        f"Last {agency} update: {updated}"
    )
