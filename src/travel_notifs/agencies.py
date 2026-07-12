from dataclasses import dataclass
from enum import StrEnum


class AgencyId(StrEnum):
    DART = "dart"
    UNITRANS = "unitrans"


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
}


def agency_for_point(latitude: float, longitude: float) -> Agency | None:
    return next(
        (agency for agency in AGENCIES.values() if agency.bounds.contains(latitude, longitude)),
        None,
    )


def agency_for_trip(origin: tuple[float, float], destination: tuple[float, float]) -> Agency | None:
    origin_agency = agency_for_point(*origin)
    destination_agency = agency_for_point(*destination)
    if origin_agency and origin_agency == destination_agency:
        return origin_agency
    return None
