import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from travel_notifs.domain import AlertState

TRIP_MONITOR_GRACE = timedelta(minutes=5)
TripView = Literal["monitored", "paused", "past"]

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    telegram_chat_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invitations (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS telegram_pairings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agency_id TEXT NOT NULL,
    origin_label TEXT NOT NULL,
    origin_place_id TEXT,
    destination_label TEXT NOT NULL,
    destination_place_id TEXT,
    timing_mode TEXT NOT NULL,
    travel_at TEXT NOT NULL,
    recurrence TEXT NOT NULL DEFAULT 'once',
    weekdays TEXT NOT NULL DEFAULT '',
    selected_itinerary TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_states (
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    leg_fingerprint TEXT NOT NULL,
    sent_milestones TEXT NOT NULL DEFAULT '',
    last_communicated_eta TEXT,
    last_sent_at TEXT,
    missing_notified INTEGER NOT NULL DEFAULT 0,
    cancellation_notified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trip_id, leg_fingerprint)
);
"""


@dataclass(frozen=True, slots=True)
class TripRecord:
    id: int
    agency_id: str
    origin_label: str
    destination_label: str
    timing_mode: str
    travel_at: str
    recurrence: str
    weekdays: str
    status: str


@dataclass(frozen=True, slots=True)
class MonitoredTrip:
    id: int
    user_id: int
    agency_id: str
    origin_place_id: str
    destination_place_id: str
    timing_mode: str
    travel_at: str
    recurrence: str
    weekdays: str
    selected_itinerary: dict[str, object]
    email: str | None
    telegram_chat_id: str | None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(trips)").fetchall()
            }
            if "selected_itinerary" not in columns:
                connection.execute(
                    "ALTER TABLE trips ADD COLUMN selected_itinerary TEXT NOT NULL DEFAULT '{}'"
                )

    def get_or_create_development_user(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if row:
                return int(row["id"])
            cursor = connection.execute(
                "INSERT INTO users(display_name, created_at) VALUES (?, ?)",
                ("Local dispatcher", datetime.now(tz=UTC).isoformat()),
            )
            return int(cursor.lastrowid)

    def user_exists(self, user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
            return row is not None

    def create_invitation(self, lifetime_hours: int = 48) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.now(tz=UTC) + timedelta(hours=lifetime_hours)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO invitations(token_hash, expires_at) VALUES (?, ?)",
                (digest, expires.isoformat()),
            )
        return token

    def consume_invitation(self, token: str, display_name: str) -> int | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(tz=UTC)
        with self.connect() as connection:
            invite = connection.execute(
                "SELECT id, expires_at, used_at FROM invitations WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            expired = invite and datetime.fromisoformat(invite["expires_at"]) < now
            if not invite or invite["used_at"] or expired:
                return None
            cursor = connection.execute(
                "INSERT INTO users(display_name, created_at) VALUES (?, ?)",
                (display_name.strip(), now.isoformat()),
            )
            connection.execute(
                "UPDATE invitations SET used_at = ? WHERE id = ?",
                (now.isoformat(), invite["id"]),
            )
            return int(cursor.lastrowid)

    def create_telegram_pairing(self, user_id: int, lifetime_minutes: int = 10) -> str:
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.now(tz=UTC) + timedelta(minutes=lifetime_minutes)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO telegram_pairings(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (user_id, digest, expires.isoformat()),
            )
        return token

    def consume_telegram_pairing(self, token: str, chat_id: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(tz=UTC)
        with self.connect() as connection:
            pairing = connection.execute(
                """
                SELECT id, user_id, expires_at, used_at
                FROM telegram_pairings WHERE token_hash = ?
                """,
                (digest,),
            ).fetchone()
            expired = pairing and datetime.fromisoformat(pairing["expires_at"]) < now
            if not pairing or pairing["used_at"] or expired:
                return False
            connection.execute(
                "UPDATE users SET telegram_chat_id = ? WHERE id = ?",
                (chat_id, pairing["user_id"]),
            )
            connection.execute(
                "UPDATE telegram_pairings SET used_at = ? WHERE id = ?",
                (now.isoformat(), pairing["id"]),
            )
        return True

    def telegram_is_paired(self, user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM users
                WHERE id = ? AND telegram_chat_id IS NOT NULL
                  AND telegram_chat_id <> ''
                """,
                (user_id,),
            ).fetchone()
        return row is not None

    def create_trip(self, user_id: int, data: dict[str, object]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trips(
                    user_id, agency_id, origin_label, origin_place_id,
                    destination_label, destination_place_id, timing_mode,
                    travel_at, recurrence, weekdays, selected_itinerary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    data["agency_id"],
                    data["origin_label"],
                    data.get("origin_place_id", ""),
                    data["destination_label"],
                    data.get("destination_place_id", ""),
                    data["timing_mode"],
                    data["travel_at"],
                    data.get("recurrence", "once"),
                    data.get("weekdays", ""),
                    json.dumps(data.get("selected_itinerary", {})),
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_trips(self, user_id: int) -> list[TripRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, agency_id, origin_label, destination_label, timing_mode,
                       travel_at, recurrence, weekdays, status
                FROM trips WHERE user_id = ? ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [TripRecord(**dict(row)) for row in rows]

    def list_dashboard_trips(
        self,
        user_id: int,
        view: TripView,
        now: datetime | None = None,
    ) -> list[TripRecord]:
        now = now or datetime.now(tz=UTC)
        return [
            _trip_record(row)
            for row in self._list_dashboard_rows(user_id)
            if _trip_view(row, now) == view
        ]

    def dashboard_trip_counts(
        self, user_id: int, now: datetime | None = None
    ) -> dict[TripView, int]:
        now = now or datetime.now(tz=UTC)
        counts: dict[TripView, int] = {"monitored": 0, "paused": 0, "past": 0}
        for row in self._list_dashboard_rows(user_id):
            counts[_trip_view(row, now)] += 1
        return counts

    def _list_dashboard_rows(self, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT id, agency_id, origin_label, destination_label, timing_mode,
                       travel_at, recurrence, weekdays, status, selected_itinerary
                FROM trips WHERE user_id = ? ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()

    def list_active_monitored_trips(self) -> list[MonitoredTrip]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT t.id, t.user_id, t.agency_id, t.origin_place_id,
                       t.destination_place_id, t.timing_mode, t.travel_at,
                       t.recurrence, t.weekdays, t.selected_itinerary,
                       u.email, u.telegram_chat_id
                FROM trips AS t
                JOIN users AS u ON u.id = t.user_id
                WHERE t.status = 'active'
                  AND t.origin_place_id <> ''
                  AND t.destination_place_id <> ''
                  AND t.selected_itinerary <> '{}'
                ORDER BY t.id
                """
            ).fetchall()
        trips = []
        for row in rows:
            values = dict(row)
            try:
                values["selected_itinerary"] = json.loads(values["selected_itinerary"])
            except (TypeError, json.JSONDecodeError):
                continue
            trips.append(MonitoredTrip(**values))
        return trips

    def delivery_exists(self, idempotency_key: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM notification_deliveries WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def record_delivery(
        self,
        trip_id: int,
        channel: str,
        recipient: str,
        idempotency_key: str,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries(
                    idempotency_key, trip_id, channel, recipient, sent_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    trip_id,
                    channel,
                    recipient,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def load_alert_state(self, trip_id: int, leg_fingerprint: str) -> AlertState:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT sent_milestones, last_communicated_eta, last_sent_at,
                       missing_notified, cancellation_notified
                FROM alert_states
                WHERE trip_id = ? AND leg_fingerprint = ?
                """,
                (trip_id, leg_fingerprint),
            ).fetchone()
        if row is None:
            return AlertState()
        return AlertState(
            sent_milestones={int(value) for value in row["sent_milestones"].split(",") if value},
            last_communicated_eta=(
                datetime.fromisoformat(row["last_communicated_eta"])
                if row["last_communicated_eta"]
                else None
            ),
            last_sent_at=(
                datetime.fromisoformat(row["last_sent_at"]) if row["last_sent_at"] else None
            ),
            missing_notified=bool(row["missing_notified"]),
            cancellation_notified=bool(row["cancellation_notified"]),
        )

    def save_alert_state(self, trip_id: int, leg_fingerprint: str, state: AlertState) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_states(
                    trip_id, leg_fingerprint, sent_milestones,
                    last_communicated_eta, last_sent_at, missing_notified,
                    cancellation_notified
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trip_id, leg_fingerprint) DO UPDATE SET
                    sent_milestones = excluded.sent_milestones,
                    last_communicated_eta = excluded.last_communicated_eta,
                    last_sent_at = excluded.last_sent_at,
                    missing_notified = excluded.missing_notified,
                    cancellation_notified = excluded.cancellation_notified
                """,
                (
                    trip_id,
                    leg_fingerprint,
                    ",".join(str(value) for value in sorted(state.sent_milestones)),
                    state.last_communicated_eta.isoformat()
                    if state.last_communicated_eta
                    else None,
                    state.last_sent_at.isoformat() if state.last_sent_at else None,
                    state.missing_notified,
                    state.cancellation_notified,
                ),
            )

    def set_trip_status(self, user_id: int, trip_id: int, status: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE trips SET status = ? WHERE id = ? AND user_id = ?",
                (status, trip_id, user_id),
            )
            return cursor.rowcount == 1


def _trip_record(row: sqlite3.Row) -> TripRecord:
    values = dict(row)
    values.pop("selected_itinerary")
    return TripRecord(**values)


def _trip_view(row: sqlite3.Row, now: datetime) -> TripView:
    if row["recurrence"] == "once" and now > _trip_boarding_time(row) + TRIP_MONITOR_GRACE:
        return "past"
    return "paused" if row["status"] == "paused" else "monitored"


def _trip_boarding_time(row: sqlite3.Row) -> datetime:
    try:
        itinerary = json.loads(row["selected_itinerary"])
        legs = itinerary.get("legs", [])
        boarding = next(
            str(leg["start_time"])
            for leg in legs
            if isinstance(leg, dict) and leg.get("fingerprint") and leg.get("start_time")
        )
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError):
        boarding = str(row["travel_at"])
    parsed = datetime.fromisoformat(boarding)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
