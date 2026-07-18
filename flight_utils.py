from __future__ import annotations

from datetime import datetime


def normalize_delta_flight_number(
    value: str,
) -> str:
    cleaned = (
        value.strip()
        .upper()
        .replace(" ", "")
        .replace("-", "")
    )

    if cleaned.startswith("DELTA"):
        cleaned = cleaned[5:]

    if cleaned.startswith("DL"):
        number = cleaned[2:]
    else:
        number = cleaned

    if not number.isdigit():
        raise ValueError(
            "Flight number must contain only numbers, "
            "optionally prefixed with DL."
        )

    number = number.lstrip("0") or "0"

    return f"DL{number}"


def calculate_delay_minutes(
    actual_or_estimated: str | None,
    scheduled: str | None,
) -> int | None:
    if not actual_or_estimated or not scheduled:
        return None

    try:
        actual_dt = datetime.fromisoformat(
            actual_or_estimated
        )
        scheduled_dt = datetime.fromisoformat(
            scheduled
        )
    except ValueError:
        return None

    return round(
        (
            actual_dt - scheduled_dt
        ).total_seconds()
        / 60
    )


def format_datetime(
    value: str | None,
) -> str:
    if not value:
        return "—"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value

    return parsed.strftime(
        "%b %d, %Y at %I:%M %p"
    )


def build_esp32_payload(
    flight: dict,
) -> dict:
    origin = flight.get("origin", {})
    destination = flight.get("destination", {})

    departure_delay = (
        origin.get("delay_minutes")
        if origin.get("delay_minutes") is not None
        else 0
    )

    arrival_delay = (
        destination.get("delay_minutes")
        if destination.get("delay_minutes") is not None
        else departure_delay
    )

    return {
        "v": 1,
        "flight": flight.get("ident", ""),
        "from": origin.get("code", "—"),
        "to": destination.get("code", "—"),
        "status": flight.get(
            "status",
            "Unknown",
        ),
        "gate_out": origin.get("gate") or "—",
        "gate_in": destination.get("gate") or "—",
        "delay": max(
            departure_delay,
            arrival_delay,
        ),
        "progress": calculate_progress(flight),
    }


def calculate_progress(
    flight: dict,
) -> int:
    origin = flight.get("origin", {})
    destination = flight.get("destination", {})

    departure = (
        origin.get("actual")
        or origin.get("estimated")
        or origin.get("scheduled")
    )

    arrival = (
        destination.get("actual")
        or destination.get("estimated")
        or destination.get("scheduled")
    )

    if not departure or not arrival:
        return 0

    try:
        departure_dt = datetime.fromisoformat(
            departure
        )
        arrival_dt = datetime.fromisoformat(
            arrival
        )
        now = datetime.now(
            departure_dt.tzinfo
        )
    except ValueError:
        return 0

    if arrival_dt <= departure_dt:
        return 0

    if now <= departure_dt:
        return 0

    if now >= arrival_dt:
        return 100

    elapsed = (
        now - departure_dt
    ).total_seconds()

    total = (
        arrival_dt - departure_dt
    ).total_seconds()

    return max(
        0,
        min(
            100,
            round(
                elapsed / total * 100
            ),
        ),
    )
