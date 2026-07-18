from __future__ import annotations

from datetime import date
import json

import streamlit as st

from delta_provider import DeltaFlightStatusClient, DeltaLookupError
from flight_utils import build_esp32_payload, format_datetime


st.set_page_config(
    page_title="Delta Flight Tracker",
    page_icon="✈️",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container {
            max-width: 1200px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        .flight-card {
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 14px;
            padding: 1.2rem;
            margin-bottom: 1rem;
        }

        .airport-code {
            font-size: 2.5rem;
            font-weight: 700;
            line-height: 1;
        }

        .muted {
            opacity: 0.72;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=120, show_spinner=False)
def load_delta_flight(
    flight_number: str,
    flight_date: date,
) -> dict:
    client = DeltaFlightStatusClient(
        headless=True,
        timeout=40,
    )
    return client.get_flight(
        flight_number=flight_number,
        flight_date=flight_date,
    )


def render_airport(
    title: str,
    airport: dict,
) -> None:
    code = airport.get("code") or "—"
    name = airport.get("name") or ""
    city = airport.get("city") or ""
    terminal = airport.get("terminal") or "—"
    gate = airport.get("gate") or "—"

    st.markdown(
        f"""
        <div class="flight-card">
            <div class="muted">{title}</div>
            <div class="airport-code">{code}</div>
            <div>{name}</div>
            <div class="muted">{city}</div>
            <br>
            <strong>Terminal:</strong> {terminal}<br>
            <strong>Gate:</strong> {gate}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write(
        "**Scheduled:**",
        format_datetime(airport.get("scheduled")),
    )
    st.write(
        "**Estimated:**",
        format_datetime(airport.get("estimated")),
    )
    st.write(
        "**Actual:**",
        format_datetime(airport.get("actual")),
    )

    delay = airport.get("delay_minutes")
    if delay is not None:
        st.write("**Delay:**", f"{delay:+d} minutes")


def main() -> None:
    st.title("Delta Flight Tracker")
    st.caption(
        "Flight details are read from Delta's public flight-status page."
    )

    with st.form("flight_lookup"):
        col1, col2 = st.columns([1, 1])

        with col1:
            flight_number = st.text_input(
                "Delta flight number",
                value="DL2738",
                placeholder="DL2738",
            )

        with col2:
            selected_date = st.date_input(
                "Flight date",
                value=date.today(),
            )

        submitted = st.form_submit_button(
            "Load flight",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        st.info("Enter a Delta flight number and date.")
        return

    flight_number = flight_number.strip().upper()

    if not flight_number:
        st.error("Enter a flight number.")
        return

    with st.spinner("Loading Delta flight details..."):
        try:
            flight = load_delta_flight(
                flight_number,
                selected_date,
            )
        except DeltaLookupError as exc:
            st.error(str(exc))
            st.stop()
        except Exception as exc:
            st.error(
                "Delta flight status could not be loaded. "
                f"Technical details: {type(exc).__name__}: {exc}"
            )
            st.stop()

    status = flight.get("status") or "Unknown"

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("Flight", flight.get("ident", flight_number))
    top2.metric("Status", status)
    top3.metric(
        "Departure delay",
        _delay_label(
            flight.get("origin", {}).get("delay_minutes")
        ),
    )
    top4.metric(
        "Arrival delay",
        _delay_label(
            flight.get("destination", {}).get("delay_minutes")
        ),
    )

    left, center, right = st.columns([1, 0.25, 1])

    with left:
        render_airport(
            "Departure",
            flight.get("origin", {}),
        )

    with center:
        st.markdown(
            "<div style='text-align:center;font-size:2rem;padding-top:5rem;'>→</div>",
            unsafe_allow_html=True,
        )

    with right:
        render_airport(
            "Arrival",
            flight.get("destination", {}),
        )

    details1, details2, details3 = st.columns(3)

    with details1:
        st.subheader("Aircraft")
        st.write(flight.get("aircraft_type") or "—")

    with details2:
        st.subheader("Baggage")
        st.write(
            flight.get("destination", {}).get("baggage_claim")
            or "—"
        )

    with details3:
        st.subheader("Updated")
        st.write(format_datetime(flight.get("updated_at")))

    payload = build_esp32_payload(flight)

    st.subheader("ESP32 payload")
    st.code(
        json.dumps(payload, indent=2),
        language="json",
    )

    with st.expander("Normalized flight data"):
        st.json(flight)

    source_url = flight.get("source_url")
    if source_url:
        st.link_button(
            "Open flight on Delta",
            source_url,
            use_container_width=True,
        )


def _delay_label(value: int | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "On time"
    if value > 0:
        return f"{value} min late"
    return f"{abs(value)} min early"


if __name__ == "__main__":
    main()
