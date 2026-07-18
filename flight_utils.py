from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo


def normalize_delta_ident(value: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", value.upper())
    match = re.fullmatch(r"(?:DL|DAL)?(\d{1,4}[A-Z]?)", cleaned)
    if not match:
        raise ValueError(
            "Enter a Delta flight number such as DL1234, DAL1234, or 1234."
        )
    return f"DAL{match.group(1)}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def choose_flight(flights: list[dict], requested_date: date) -> dict:
    if not flights:
        raise ValueError("No flight records were returned.")

    def score(flight: dict) -> tuple[int, float]:
        departure = (
            parse_dt(flight.get("origin", {}).get("scheduled"))
            or parse_dt(flight.get("origin", {}).get("estimated"))
            or parse_dt(flight.get("origin", {}).get("actual"))
        )
        if departure is None:
            return (1, float("inf"))

        same_date = 0 if departure.date() == requested_date else 1
        noon = datetime.combine(
            requested_date,
            datetime.min.time(),
            tzinfo=departure.tzinfo,
        ) + timedelta(hours=12)
        return (same_date, abs((departure - noon).total_seconds()))

    return min(flights, key=score)


def format_clock(value: str | None, include_zone: bool = False) -> str:
    dt = parse_dt(value)
    if dt is None:
        return "—"
    try:
        local = dt.astimezone()
    except Exception:
        local = dt
    text = local.strftime("%-I:%M %p")
    if include_zone:
        text += f" {local.tzname() or ''}".rstrip()
    return text


def format_duration(minutes: int | float | None) -> str:
    if minutes is None:
        return "—"
    minutes = max(0, round(minutes))
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def progress_percent(flight: dict) -> int:
    status = str(flight.get("status", "")).lower()
    if "landed" in status or "arrived" in status:
        return 100
    if flight.get("cancelled"):
        return 0

    total = flight.get("route_distance_nm")
    flown = flight.get("distance_flown_nm")
    if isinstance(total, (int, float)) and total > 0 and isinstance(flown, (int, float)):
        return max(0, min(100, round(flown / total * 100)))

    departure = (
        parse_dt(flight.get("origin", {}).get("actual"))
        or parse_dt(flight.get("origin", {}).get("estimated"))
    )
    arrival = (
        parse_dt(flight.get("destination", {}).get("actual"))
        or parse_dt(flight.get("destination", {}).get("estimated"))
    )
    now = datetime.now(timezone.utc)
    if departure and arrival and arrival > departure:
        progress = (now - departure).total_seconds() / (
            arrival - departure
        ).total_seconds()
        return max(0, min(100, round(progress * 100)))
    return 0


def build_esp32_payload(flight: dict) -> dict[str, Any]:
    """Compact fields intended for a small display and inexpensive polling."""
    return {
        "v": 1,
        "flight": flight.get("ident"),
        "from": flight.get("origin", {}).get("code"),
        "to": flight.get("destination", {}).get("code"),
        "status": flight.get("status"),
        "gate_out": flight.get("origin", {}).get("gate"),
        "gate_in": flight.get("destination", {}).get("gate"),
        "dep": flight.get("origin", {}).get("estimated")
               or flight.get("origin", {}).get("scheduled"),
        "arr": flight.get("destination", {}).get("estimated")
               or flight.get("destination", {}).get("scheduled"),
        "delay": flight.get("destination", {}).get("delay_minutes", 0),
        "progress": progress_percent(flight),
        "alt_ft": flight.get("altitude_ft"),
        "speed_kt": flight.get("ground_speed_kts"),
        "remaining_min": flight.get("minutes_remaining"),
        "updated": flight.get("updated_at"),
    }


def demo_flight(ident: str, flight_date: date) -> dict:
    now = datetime.now(timezone.utc)
    departure = now - timedelta(minutes=73)
    arrival = now + timedelta(minutes=42)
    scheduled_departure = departure - timedelta(minutes=18)
    scheduled_arrival = arrival - timedelta(minutes=7)

    return {
        "fa_flight_id": "DEMO-DELTA-001",
        "ident": ident.replace("DAL", "DL"),
        "ident_icao": ident,
        "status": "En Route",
        "cancelled": False,
        "diverted": False,
        "origin": {
            "code": "ATL",
            "icao": "KATL",
            "name": "Hartsfield-Jackson Atlanta International",
            "city": "Atlanta",
            "terminal": "S",
            "gate": "B18",
            "scheduled": scheduled_departure.isoformat(),
            "estimated": departure.isoformat(),
            "actual": departure.isoformat(),
            "delay_minutes": 18,
        },
        "destination": {
            "code": "MCO",
            "icao": "KMCO",
            "name": "Orlando International",
            "city": "Orlando",
            "terminal": "B",
            "gate": "72",
            "scheduled": scheduled_arrival.isoformat(),
            "estimated": arrival.isoformat(),
            "actual": None,
            "delay_minutes": 7,
        },
        "aircraft_type": "A321",
        "registration": "N123DN",
        "scheduled_minutes": 122,
        "flight_minutes": 115,
        "route_distance_nm": 523,
        "distance_flown_nm": 332,
        "distance_remaining_nm": 191,
        "minutes_remaining": 42,
        "latitude": 31.58,
        "longitude": -82.15,
        "altitude_ft": 35000,
        "ground_speed_kts": 462,
        "updated_at": now.isoformat(),
        "raw_source": "demo",
    }
