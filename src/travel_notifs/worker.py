import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from travel_notifs.agencies import AGENCIES, AgencyId
from travel_notifs.alerts import AlertEngine
from travel_notifs.config import get_settings
from travel_notifs.domain import PredictionStatus, StopPrediction
from travel_notifs.notifications import NotificationError, ResendNotifier, TelegramNotifier
from travel_notifs.planning import GoogleTransitProvider, Itinerary, Leg, PlanningError
from travel_notifs.storage import TRIP_MONITOR_GRACE, Database, MonitoredTrip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("travel_notifs.worker")

MONITOR_LEAD = timedelta(minutes=20)
MATCH_WINDOW = timedelta(minutes=20)


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.database = Database(self.settings.database_path)
        self.database.initialize()
        self.stopping = asyncio.Event()
        self.provider = (
            GoogleTransitProvider(self.settings.google_maps_api_key)
            if self.settings.google_maps_api_key
            else None
        )
        self.alert_engine = AlertEngine(
            eta_change_minutes=self.settings.eta_change_threshold_minutes,
            cooldown_seconds=self.settings.notification_cooldown_seconds,
        )
        self.telegram = (
            TelegramNotifier(self.settings.telegram_bot_token)
            if self.settings.telegram_bot_token
            else None
        )
        self.email = (
            ResendNotifier(self.settings.resend_api_key, self.settings.email_from)
            if self.settings.resend_api_key
            else None
        )

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(tz=UTC)
        if not self.provider:
            logger.info("monitor tick skipped: Google Maps API key is not configured")
            return

        monitored = self.database.list_active_monitored_trips()
        due = [(trip, request_at) for trip in monitored if (request_at := due_request(trip, now))]
        logger.info("monitor tick active=%s due=%s", len(monitored), len(due))
        for trip, request_at in due:
            try:
                itineraries = await self.provider.plan(
                    AgencyId(trip.agency_id),
                    trip.origin_place_id,
                    trip.destination_place_id,
                    request_at,
                    trip.timing_mode == "arrive",
                )
                await self._evaluate_trip(trip, itineraries, request_at, now)
            except (PlanningError, ValueError):
                logger.exception("refresh failed trip_id=%s", trip.id)

    async def _evaluate_trip(
        self,
        trip: MonitoredTrip,
        itineraries: list[Itinerary],
        request_at: datetime,
        now: datetime,
    ) -> None:
        selected = selected_transit_legs(trip.selected_itinerary)
        original_request = parse_datetime(trip.travel_at)
        shift = request_at - original_request
        for original in selected:
            expected = parse_datetime(str(original["start_time"])) + shift
            matched = match_leg(str(original["fingerprint"]), expected, itineraries)
            if matched is None:
                logger.warning(
                    "selected leg not found trip_id=%s fingerprint=%s",
                    trip.id,
                    original["fingerprint"],
                )
                continue
            prediction = StopPrediction(
                agency_id=AgencyId(trip.agency_id),
                trip_id=matched.fingerprint,
                stop_id=matched.fingerprint,
                route_label=matched.route,
                headsign=matched.headsign,
                stop_name=matched.from_name,
                scheduled_arrival=None,
                predicted_arrival=parse_datetime(matched.start_time),
                feed_timestamp=now,
                status=PredictionStatus.LIVE,
            )
            state = self.database.load_alert_state(trip.id, matched.fingerprint)
            decision = self.alert_engine.evaluate(prediction, state, now)
            self.database.save_alert_state(trip.id, matched.fingerprint, state)
            if decision.should_send:
                await self._deliver(trip, decision.message, decision.idempotency_key)

    async def _deliver(self, trip: MonitoredTrip, message: str, base_key: str) -> None:
        if self.settings.dry_run:
            logger.info("dry-run notification trip_id=%s\n%s", trip.id, message)
            return

        deliveries = []
        if self.telegram and trip.telegram_chat_id:
            deliveries.append(("telegram", trip.telegram_chat_id, self.telegram))
        if self.email and trip.email:
            deliveries.append(("email", trip.email, self.email))
        if not deliveries:
            logger.warning("notification skipped: no paired channel trip_id=%s", trip.id)
            return

        for channel, recipient, notifier in deliveries:
            delivery_key = sha256(f"{base_key}:{channel}".encode()).hexdigest()
            if self.database.delivery_exists(delivery_key):
                continue
            await notifier.send(recipient, message, delivery_key)
            self.database.record_delivery(trip.id, channel, recipient, delivery_key)

    async def monitor_loop(self) -> None:
        logger.info("monitor worker started interval=%ss", self.settings.poll_interval_seconds)
        while not self.stopping.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("monitor tick failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self.stopping.wait(), timeout=self.settings.poll_interval_seconds
                )
        logger.info("monitor worker stopped")

    async def telegram_loop(self) -> None:
        if self.telegram is None:
            return
        offset: int | None = None
        logger.info("Telegram pairing listener started")
        while not self.stopping.is_set():
            try:
                for update in await self.telegram.updates(offset):
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    await self._handle_telegram_update(update)
            except NotificationError:
                logger.exception("Telegram polling failed")
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), timeout=5)
        logger.info("Telegram pairing listener stopped")

    async def _handle_telegram_update(self, update: dict[str, object]) -> None:
        if self.telegram is None:
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat = message.get("chat")
        if not isinstance(text, str) or not isinstance(chat, dict) or "id" not in chat:
            return
        if not text.startswith("/start "):
            return
        token = text.split(maxsplit=1)[1].strip()
        chat_id = str(chat["id"])
        paired = self.database.consume_telegram_pairing(token, chat_id)
        response = (
            "Connected to Transit Dispatch. Arrival notifications will appear here."
            if paired
            else "This connection link is invalid or expired. Create a new one in Transit Dispatch."
        )
        await self.telegram.send(chat_id, response, f"pairing:{update.get('update_id', '')}")

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.monitor_loop())
            if self.telegram:
                tasks.create_task(self.telegram_loop())


def due_request(trip: MonitoredTrip, now: datetime) -> datetime | None:
    original_request = parse_datetime(trip.travel_at)
    legs = selected_transit_legs(trip.selected_itinerary)
    if not legs:
        return None
    original_boarding = parse_datetime(str(legs[0]["start_time"]))
    if trip.recurrence == "once":
        return (
            original_request
            if original_boarding - MONITOR_LEAD <= now
            <= original_boarding + TRIP_MONITOR_GRACE
            else None
        )

    agency_zone = ZoneInfo(AGENCIES[AgencyId(trip.agency_id)].timezone)
    local_now = now.astimezone(agency_zone)
    local_request = original_request.astimezone(agency_zone)
    boarding_offset = original_boarding.astimezone(agency_zone) - local_request
    weekdays = {int(value) for value in trip.weekdays.split(",") if value.isdigit()}
    if not weekdays:
        weekdays = {local_request.weekday()}
    for day_offset in (-1, 0, 1):
        candidate_date = local_now.date() + timedelta(days=day_offset)
        if candidate_date.weekday() not in weekdays:
            continue
        candidate_request = datetime.combine(
            candidate_date,
            time(local_request.hour, local_request.minute, local_request.second),
            tzinfo=agency_zone,
        )
        candidate_boarding = candidate_request + boarding_offset
        if (
            candidate_boarding - MONITOR_LEAD
            <= local_now
            <= candidate_boarding + TRIP_MONITOR_GRACE
        ):
            return candidate_request.astimezone(UTC)
    return None


def selected_transit_legs(itinerary: dict[str, object]) -> list[dict[str, object]]:
    legs = itinerary.get("legs", [])
    if not isinstance(legs, list):
        return []
    return [
        leg
        for leg in legs
        if isinstance(leg, dict) and leg.get("fingerprint") and leg.get("start_time")
    ]


def match_leg(fingerprint: str, expected: datetime, itineraries: list[Itinerary]) -> Leg | None:
    candidates = [
        leg
        for itinerary in itineraries
        for leg in itinerary.legs
        if leg.fingerprint == fingerprint and leg.start_time
    ]
    candidates.sort(key=lambda leg: abs(parse_datetime(leg.start_time) - expected))
    if not candidates:
        return None
    closest = candidates[0]
    if abs(parse_datetime(closest.start_time) - expected) > MATCH_WINDOW:
        return None
    return closest


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def main() -> None:
    worker = Worker()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, worker.stopping.set)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
