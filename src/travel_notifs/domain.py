from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from travel_notifs.agencies import AgencyId


class PredictionStatus(StrEnum):
    LIVE = "live"
    SCHEDULED_ONLY = "scheduled_only"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StopPrediction:
    agency_id: AgencyId
    trip_id: str
    stop_id: str
    route_label: str
    headsign: str
    stop_name: str
    scheduled_arrival: datetime | None
    predicted_arrival: datetime | None
    feed_timestamp: datetime
    status: PredictionStatus = PredictionStatus.LIVE

    @property
    def effective_arrival(self) -> datetime:
        arrival = self.predicted_arrival or self.scheduled_arrival
        if arrival is None:
            raise ValueError("Prediction has no arrival time")
        return arrival

    @property
    def delay_minutes(self) -> int | None:
        if self.scheduled_arrival is None:
            return None
        delta = self.effective_arrival - self.scheduled_arrival
        return round(delta.total_seconds() / 60)

    def is_stale(self, now: datetime, threshold_seconds: int) -> bool:
        return (now - self.feed_timestamp).total_seconds() > threshold_seconds


@dataclass(slots=True)
class AlertState:
    sent_milestones: set[int] = field(default_factory=set)
    last_communicated_eta: datetime | None = None
    last_sent_at: datetime | None = None
    missing_notified: bool = False
    cancellation_notified: bool = False


@dataclass(frozen=True, slots=True)
class AlertDecision:
    should_send: bool
    reasons: tuple[str, ...] = ()
    message: str = ""
    idempotency_key: str = ""


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
