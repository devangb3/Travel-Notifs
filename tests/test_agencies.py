from travel_notifs.agencies import AgencyId, agency_for_trip


def test_detects_dart_region() -> None:
    agency = agency_for_trip((32.7767, -96.7970), (32.9841, -96.7501))
    assert agency is not None
    assert agency.id == AgencyId.DART


def test_detects_unitrans_region() -> None:
    agency = agency_for_trip((38.5382, -121.7617), (38.5449, -121.7405))
    assert agency is not None
    assert agency.id == AgencyId.UNITRANS


def test_rejects_cross_region_trip() -> None:
    assert agency_for_trip((32.7767, -96.7970), (38.5382, -121.7617)) is None
