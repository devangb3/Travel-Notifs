# Google Routes Provider Design

## Decision

Use Google Places and Google Routes as the only launch data provider for DART,
Unitrans, and other transit agencies covered by Google. Remove OpenTripPlanner,
direct GTFS-Realtime clients, agency API credentials, graph builds, and
agency-specific realtime polling from the launch architecture.

Keep a narrow internal provider interface so a direct GTFS-Realtime or other
agency adapter can be added later without changing trip storage, alert rules,
notification delivery, or the web interface.

## Why this fits the product

The product needs user-friendly address search, transit itineraries, boarding
stops, transfers, route/headsign information, and the estimated time a vehicle
will reach a boarding stop. It does not need vehicle maps, raw GPS positions,
or independent ETA prediction.

Google Places supplies stable Place IDs and address autocomplete. Google Routes
supplies normalized transit itineraries across agencies, including estimated
arrival and departure times at each transit stop. This gives DART, Unitrans,
and future supported agencies one response contract and removes two Java
routing services from the VPS.

## Data flow

1. The browser requests address suggestions from the application backend.
2. The backend proxies Google Places Autocomplete using a server-side key.
3. The user selects origin and destination Place IDs.
4. The backend calls Google Routes with `travelMode: TRANSIT` and the selected
   depart-at or arrive-by time.
5. Google responses are normalized into application itineraries and transit
   legs.
6. The user selects an itinerary. The application stores the request and a
   fingerprint for every boarding leg.
7. During a bounded monitoring window, the worker repeats the Routes request
   and matches the selected leg using the fingerprint.
8. Updated stop departure times enter the existing alert state machine.
9. Telegram or email receives milestone and material ETA-change messages.

## Trip identity

Google Routes does not expose the agency's stable GTFS `trip_id`. Each transit
leg therefore uses a fingerprint containing:

- Agency name.
- Route short name.
- Headsign.
- Boarding stop name and coordinates.
- Alighting stop name.
- Original departure time rounded to a small matching window.

On each refresh, candidates must match the categorical fields. The closest
departure time within the permitted window is selected. If no unambiguous
candidate exists, the system reports live prediction unavailable instead of
silently switching the user to a different vehicle.

## Polling and cost control

Routes requests are made only for active monitoring instances, never for every
saved recurring definition. The default interval is two minutes and the active
window begins before the first milestone and ends after the selected vehicle
should have departed.

Equivalent requests within the same polling cycle are deduplicated. Results
are cached briefly so users monitoring the same trip share one response.
Application quotas and billing alerts are mandatory. The worker records request
counts by user and provider so actual usage can be compared with the free
monthly allowance.

## Failure behavior

Google errors, missing transit legs, ambiguous matching, or expired results are
not presented as delays. The user receives at most one unavailable-prediction
message per outage. The worker retries with backoff and can send one recovery
message when an unambiguous estimate returns.

The response does not reliably distinguish scheduled from realtime estimates.
Notifications therefore say `Expected at` and do not claim that a vehicle is a
specific number of minutes early or late unless a future provider supplies a
scheduled baseline with that guarantee.

## Deployment impact

The VPS now runs only Caddy, FastAPI, the monitoring worker, and SQLite. A 1 GB
instance may be sufficient, while 2 GB provides safer operational headroom.
The existing 4 GB development target remains more than adequate.

The Google key is server-side and restricted to Places API and Routes API.
Daily quotas limit accidental cost. No DART or Unitrans credentials are needed.

## Validation

- Contract tests use recorded representative Google Routes payloads for DART,
  Unitrans, walking legs, transfers, and missing transit results.
- Matching tests cover shifted predictions, multiple same-route candidates,
  ambiguous results, and missed vehicles.
- Live dry-run validation compares returned estimates with Google Maps and
  observed DART and Unitrans arrivals before notifications are enabled.
- Existing alert-engine, invitation, persistence, and notification-provider
  tests remain unchanged except that early/late wording is removed when no
  scheduled baseline exists.

