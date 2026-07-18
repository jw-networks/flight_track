from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any


def normalize_delta_flight_number(value: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", value.upper())
    match = re.fullmatch(r"(?:DL|DAL)?(\d{1,4}[A-Z]?)", cleaned)
    if not match:
        raise ValueError(
            "Enter a Delta flight number such as DL1234, DAL1234, or 1234."
        )
    return f"DL{match.group(1)}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def calculate_delay_minutes(
    current_value: str | None,
    scheduled_value: str | None,
) -> int:
    current = parse_dt(current_value)
    scheduled = parse_dt(scheduled_value)
    if not current or not scheduled:
        return 0
    return max(0, round((current - scheduled).total_seconds() / 60))


def format_clock(value: str | None, include_zone: bool = False) -> str:
    dt = parse_dt(value)
    if dt is None:
        return "—"
    text = dt.strftime("%-I:%M %p")
    if include_zone and dt.tzinfo:
        text += f" {dt.tzname() or ''}".rstrip()
    return text


def format_duration(minutes: int | float | None) -> str:
    if minutes is None:
        return "—"
    hours, mins = divmod(max(0, round(minutes)), 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def progress_percent(flight: dict) -> int:
    status = str(flight.get("status", "")).lower()

    if status in {"arrived", "landed"}:
        return 100
    if flight.get("cancelled"):
        return 0

    departure = (
        parse_dt(flight.get("origin", {}).get("actual"))
        or parse_dt(flight.get("origin", {}).get("estimated"))
        or parse_dt(flight.get("origin", {}).get("scheduled"))
    )
    arrival = (
        parse_dt(flight.get("destination", {}).get("actual"))
        or parse_dt(flight.get("destination", {}).get("estimated"))
        or parse_dt(flight.get("destination", {}).get("scheduled"))
    )

    if not departure or not arrival or arrival <= departure:
        return 0

    now = datetime.now(departure.tzinfo or timezone.utc)
    fraction = (now - departure).total_seconds() / (
        arrival - departure
    ).total_seconds()
    return max(0, min(100, round(fraction * 100)))


def build_esp32_payload(flight: dict) -> dict[str, Any]:
    return {
        "v": 1,
        "flight": flight.get("ident"),
        "from": flight.get("origin", {}).get("code"),
        "to": flight.get("destination", {}).get("code"),
        "status": flight.get("status"),
        "gate_out": flight.get("origin", {}).get("gate"),
        "gate_in": flight.get("destination", {}).get("gate"),
        "terminal_out": flight.get("origin", {}).get("terminal"),
        "terminal_in": flight.get("destination", {}).get("terminal"),
        "baggage": flight.get("destination", {}).get("baggage_claim"),
        "dep": (
            flight.get("origin", {}).get("estimated")
            or flight.get("origin", {}).get("scheduled")
        ),
        "arr": (
            flight.get("destination", {}).get("estimated")
            or flight.get("destination", {}).get("scheduled")
        ),
        "delay": flight.get("destination", {}).get("delay_minutes", 0),
        "progress": progress_percent(flight),
        "updated": flight.get("updated_at"),
    }


def demo_flight(flight_number: str, flight_date: date) -> dict:
    now = datetime.now(timezone.utc)
    actual_departure = now - timedelta(minutes=63)
    estimated_arrival = now + timedelta(minutes=49)

    scheduled_departure = actual_departure - timedelta(minutes=17)
    scheduled_arrival = estimated_arrival - timedelta(minutes=8)

    return {
        "ident": normalize_delta_flight_number(flight_number),
        "status": "En Route",
        "cancelled": False,
        "diverted": False,
        "origin": {
            "code": "ATL",
            "name": "Hartsfield-Jackson Atlanta International",
            "city": "Atlanta",
            "terminal": "S",
            "gate": "B18",
            "scheduled": scheduled_departure.isoformat(),
            "estimated": actual_departure.isoformat(),
            "actual": actual_departure.isoformat(),
            "delay_minutes": 17,
        },
        "destination": {
            "code": "MCO",
            "name": "Orlando International",
            "city": "Orlando",
            "terminal": "B",
            "gate": "72",
            "baggage_claim": "28",
            "scheduled": scheduled_arrival.isoformat(),
            "estimated": estimated_arrival.isoformat(),
            "actual": None,
            "delay_minutes": 8,
        },
        "aircraft_type": "Airbus A321",
        "registration": None,
        "scheduled_minutes": 120,
        "flight_minutes": 112,
        "minutes_remaining": 49,
        "updated_at": now.isoformat(),
        "raw_source": "demo",
    }
