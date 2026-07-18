from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests


class FlightLookupError(RuntimeError):
    pass


class FlightAwareClient:
    BASE_URL = "https://aeroapi.flightaware.com/aeroapi"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-apikey": api_key,
                "Accept": "application/json",
                "User-Agent": "DeltaFlightDashboard/1.0",
            }
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}",
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FlightLookupError(f"Could not reach FlightAware: {exc}") from exc

        if response.status_code == 401:
            raise FlightLookupError("FlightAware rejected the API key.")
        if response.status_code == 429:
            raise FlightLookupError("FlightAware rate limit reached. Try again later.")
        if not response.ok:
            detail = response.text[:300].strip()
            raise FlightLookupError(
                f"FlightAware returned HTTP {response.status_code}: {detail}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise FlightLookupError("FlightAware returned invalid JSON.") from exc

    def get_flights(self, ident: str, flight_date: date) -> list[dict]:
        # The active-flight endpoint uses a bounded time window. This window
        # captures flights around the selected date while avoiding unrelated
        # instances of the same daily flight number.
        start = datetime.combine(
            flight_date - timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        end = datetime.combine(
            flight_date + timedelta(days=2),
            time.min,
            tzinfo=timezone.utc,
        )

        payload = self._get(
            f"/flights/{ident}",
            params={
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "max_pages": 1,
            },
        )
        raw_flights = payload.get("flights", [])
        if not raw_flights:
            raise FlightLookupError(
                f"No FlightAware records were found for {ident} on {flight_date}."
            )
        return [self._normalize(item) for item in raw_flights]

    @staticmethod
    def _airport(item: dict, prefix: str) -> dict:
        airport = item.get(prefix) or {}
        return {
            "code": (
                airport.get("code_iata")
                or airport.get("code")
                or airport.get("code_icao")
                or "—"
            ),
            "icao": airport.get("code_icao"),
            "name": airport.get("name") or "",
            "city": airport.get("city") or "",
            "terminal": item.get(f"terminal_{prefix}"),
            "gate": item.get(f"gate_{prefix}"),
        }

    @staticmethod
    def _minutes_between(later: str | None, earlier: str | None) -> int:
        if not later or not earlier:
            return 0
        try:
            a = datetime.fromisoformat(later.replace("Z", "+00:00"))
            b = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
            return round((a - b).total_seconds() / 60)
        except (ValueError, TypeError):
            return 0

    def _normalize(self, item: dict) -> dict:
        origin = self._airport(item, "origin")
        destination = self._airport(item, "destination")

        origin["scheduled"] = item.get("scheduled_out")
        origin["estimated"] = item.get("estimated_out")
        origin["actual"] = item.get("actual_out")
        origin["delay_minutes"] = self._minutes_between(
            item.get("actual_out") or item.get("estimated_out"),
            item.get("scheduled_out"),
        )

        destination["scheduled"] = item.get("scheduled_in")
        destination["estimated"] = item.get("estimated_in")
        destination["actual"] = item.get("actual_in")
        destination["delay_minutes"] = self._minutes_between(
            item.get("actual_in") or item.get("estimated_in"),
            item.get("scheduled_in"),
        )

        route_distance = item.get("route_distance")
        filed_airspeed = item.get("filed_airspeed")

        scheduled_minutes = self._minutes_between(
            item.get("scheduled_in"), item.get("scheduled_out")
        )
        flight_minutes = self._minutes_between(
            item.get("actual_in") or item.get("estimated_in"),
            item.get("actual_out") or item.get("estimated_out"),
        )

        # Live position fields can vary by endpoint and subscription.
        last_position = item.get("last_position") or {}
        altitude = last_position.get("altitude")
        altitude_ft = altitude * 100 if isinstance(altitude, (int, float)) else None

        return {
            "fa_flight_id": item.get("fa_flight_id"),
            "ident": item.get("ident_iata") or item.get("ident") or "",
            "ident_icao": item.get("ident_icao"),
            "status": item.get("status") or "Unknown",
            "cancelled": bool(item.get("cancelled")),
            "diverted": bool(item.get("diverted")),
            "origin": origin,
            "destination": destination,
            "aircraft_type": item.get("aircraft_type"),
            "registration": item.get("registration"),
            "scheduled_minutes": scheduled_minutes or None,
            "flight_minutes": flight_minutes or None,
            "route_distance_nm": route_distance,
            "distance_flown_nm": None,
            "distance_remaining_nm": None,
            "minutes_remaining": self._minutes_between(
                item.get("actual_in") or item.get("estimated_in"),
                datetime.now(timezone.utc).isoformat(),
            ),
            "latitude": last_position.get("latitude"),
            "longitude": last_position.get("longitude"),
            "altitude_ft": altitude_ft,
            "ground_speed_kts": last_position.get("groundspeed") or filed_airspeed,
            "updated_at": (
                last_position.get("timestamp")
                or item.get("actual_off")
                or item.get("estimated_out")
                or datetime.now(timezone.utc).isoformat()
            ),
            "raw_source": "flightaware",
        }
