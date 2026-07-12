from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo


class AgencyId(StrEnum):
    DART = "dart"
    UNITRANS = "unitrans"
    YOLOBUS = "yolobus"


@dataclass(frozen=True, slots=True)
class Bounds:
    south: float
    west: float
    north: float
    east: float

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east


@dataclass(frozen=True, slots=True)
class Agency:
    id: AgencyId
    name: str
    short_name: str
    timezone: str
    bounds: Bounds
    google_names: tuple[str, ...]


AGENCIES: dict[AgencyId, Agency] = {
    AgencyId.DART: Agency(
        id=AgencyId.DART,
        name="Dallas Area Rapid Transit",
        short_name="DART",
        timezone="America/Chicago",
        bounds=Bounds(south=32.55, west=-97.10, north=33.20, east=-96.45),
        google_names=("Dallas Area Rapid Transit", "DART"),
    ),
    AgencyId.UNITRANS: Agency(
        id=AgencyId.UNITRANS,
        name="Unitrans",
        short_name="Unitrans",
        timezone="America/Los_Angeles",
        bounds=Bounds(south=38.48, west=-121.83, north=38.61, east=-121.66),
        google_names=("Unitrans",),
    ),
    AgencyId.YOLOBUS: Agency(
        id=AgencyId.YOLOBUS,
        name="Yolo County Transportation District",
        short_name="Yolobus",
        timezone="America/Los_Angeles",
        bounds=Bounds(south=38.48, west=-121.85, north=38.75, east=-121.45),
        google_names=("Yolo County Transportation District", "Yolobus"),
    ),
}


def agency_for_point(latitude: float, longitude: float) -> Agency | None:
    matches = [
        agency for agency in AGENCIES.values() if agency.bounds.contains(latitude, longitude)
    ]
    return min(matches, key=_coverage_area, default=None)


def agency_for_trip(origin: tuple[float, float], destination: tuple[float, float]) -> Agency | None:
    origin_matches = {
        agency.id: agency
        for agency in AGENCIES.values()
        if agency.bounds.contains(*origin)
    }
    destination_ids = {
        agency.id for agency in AGENCIES.values() if agency.bounds.contains(*destination)
    }
    common = [
        agency for agency_id, agency in origin_matches.items() if agency_id in destination_ids
    ]
    return min(common, key=_coverage_area, default=None)


def _coverage_area(agency: Agency) -> float:
    return (agency.bounds.north - agency.bounds.south) * (
        agency.bounds.east - agency.bounds.west
    )


def agency_local_time(value: datetime, agency_id: AgencyId | str) -> datetime:
    agency = AGENCIES[AgencyId(agency_id)]
    return value.astimezone(ZoneInfo(agency.timezone))
