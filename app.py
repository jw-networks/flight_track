from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
import time

import streamlit as st

from flight_provider import FlightAwareClient, FlightLookupError
from flight_utils import (
    build_esp32_payload,
    choose_flight,
    demo_flight,
    format_clock,
    format_duration,
    normalize_delta_ident,
    progress_percent,
)

st.set_page_config(
    page_title="Delta Flight Dashboard",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.7rem; padding-bottom: 2rem;}
      [data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 12px;
        padding: 12px 14px;
        background: rgba(128,128,128,.055);
      }
      .flight-banner {
        border-radius: 16px;
        padding: 18px 22px;
        margin-bottom: 18px;
        background: linear-gradient(120deg, #7a0019, #b0002b);
        color: white;
      }
      .flight-banner h2 {margin: 0; padding: 0;}
      .flight-banner p {margin: 5px 0 0 0; opacity: .88;}
      .small-label {opacity: .7; font-size: .82rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

def get_api_key() -> str:
    """Read the key from Streamlit secrets first, then the environment."""
    try:
        return str(st.secrets.get("FLIGHTAWARE_API_KEY", "")).strip()
    except Exception:
        return os.getenv("FLIGHTAWARE_API_KEY", "").strip()

def load_flight(ident: str, flight_date: date, demo_mode: bool) -> dict:
    if demo_mode:
        return demo_flight(ident, flight_date)

    api_key = get_api_key()
    if not api_key:
        raise FlightLookupError(
            "No FlightAware API key was found. Add it to "
            "`.streamlit/secrets.toml` or enable Demo mode."
        )

    client = FlightAwareClient(api_key=api_key)
    candidates = client.get_flights(ident, flight_date)
    return choose_flight(candidates, flight_date)

def show_time_column(title: str, airport: dict, scheduled_key: str,
                     estimated_key: str, actual_key: str) -> None:
    st.subheader(title)
    st.markdown(
        f"### {airport.get('code', '—')}  \n"
        f"{airport.get('name', '')}"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Scheduled", format_clock(airport.get(scheduled_key)))
    c2.metric("Estimated", format_clock(airport.get(estimated_key)))
    c3.metric("Actual", format_clock(airport.get(actual_key)))

    d1, d2, d3 = st.columns(3)
    d1.metric("Terminal", airport.get("terminal") or "—")
    d2.metric("Gate", airport.get("gate") or "—")
    d3.metric(
        "Delay",
        f"{airport.get('delay_minutes', 0)} min",
        delta=None,
    )

with st.sidebar:
    st.title("Flight Search")
    with st.form("flight_search"):
        flight_number = st.text_input(
            "Delta flight number",
            value=st.session_state.get("flight_number", "DL1234"),
            placeholder="DL1234 or 1234",
        )
        flight_date = st.date_input(
            "Flight date",
            value=st.session_state.get("flight_date", date.today()),
        )
        demo_mode = st.toggle(
            "Demo mode",
            value=st.session_state.get("demo_mode", True),
            help="Uses realistic sample data and does not call FlightAware.",
        )
        submitted = st.form_submit_button(
            "Load flight",
            type="primary",
            use_container_width=True,
        )

    st.divider()
    auto_refresh = st.toggle(
        "Auto-refresh",
        value=False,
        help="Refreshes the page at the selected interval.",
    )
    refresh_seconds = st.select_slider(
        "Refresh interval",
        options=[30, 60, 120, 300],
        value=60,
        format_func=lambda value: f"{value} sec",
        disabled=not auto_refresh,
    )

if submitted:
    st.session_state.flight_number = flight_number
    st.session_state.flight_date = flight_date
    st.session_state.demo_mode = demo_mode
    st.session_state.should_load = True

st.title("Delta Flight Dashboard")
st.caption("Live flight status now; compact ESP32 display payload later.")

if not st.session_state.get("should_load", False):
    st.info("Enter a Delta flight number in the sidebar and select **Load flight**.")
    st.stop()

try:
    ident = normalize_delta_ident(st.session_state.flight_number)
    with st.spinner(f"Loading {ident}…"):
        flight = load_flight(
            ident,
            st.session_state.flight_date,
            st.session_state.demo_mode,
        )
except (ValueError, FlightLookupError) as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:
    st.error(f"Unexpected error: {exc}")
    st.stop()

origin = flight["origin"]
destination = flight["destination"]
status = flight.get("status", "Unknown")
progress = progress_percent(flight)

st.markdown(
    f"""
    <div class="flight-banner">
      <h2>{flight.get('ident', ident)} · {origin.get('code', '—')} → {destination.get('code', '—')}</h2>
      <p>{status} · {flight.get('aircraft_type') or 'Aircraft pending'} ·
      Updated {format_clock(flight.get('updated_at'), include_zone=True)}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

top1, top2, top3, top4, top5 = st.columns(5)
top1.metric("Status", status)
top2.metric("Progress", f"{progress}%")
top3.metric(
    "Arrival delay",
    f"{destination.get('delay_minutes', 0)} min",
)
top4.metric(
    "Time remaining",
    format_duration(flight.get("minutes_remaining")),
)
top5.metric(
    "Distance remaining",
    f"{flight.get('distance_remaining_nm', 0):,.0f} nm"
    if flight.get("distance_remaining_nm") is not None else "—",
)

st.progress(progress / 100, text=f"{progress}% complete")

left, right = st.columns(2)
with left:
    show_time_column(
        "Departure",
        origin,
        "scheduled",
        "estimated",
        "actual",
    )
with right:
    show_time_column(
        "Arrival",
        destination,
        "scheduled",
        "estimated",
        "actual",
    )

st.divider()
st.subheader("Flight Metrics")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Altitude", f"{flight.get('altitude_ft', 0):,.0f} ft"
          if flight.get("altitude_ft") is not None else "—")
m2.metric("Ground speed", f"{flight.get('ground_speed_kts', 0):,.0f} kt"
          if flight.get("ground_speed_kts") is not None else "—")
m3.metric("Aircraft", flight.get("aircraft_type") or "—")
m4.metric("Registration", flight.get("registration") or "—")

m5, m6, m7, m8 = st.columns(4)
m5.metric("Flight time", format_duration(flight.get("flight_minutes")))
m6.metric("Scheduled time", format_duration(flight.get("scheduled_minutes")))
m7.metric("Distance flown", f"{flight.get('distance_flown_nm', 0):,.0f} nm"
          if flight.get("distance_flown_nm") is not None else "—")
m8.metric("Total distance", f"{flight.get('route_distance_nm', 0):,.0f} nm"
          if flight.get("route_distance_nm") is not None else "—")

if flight.get("latitude") is not None and flight.get("longitude") is not None:
    st.subheader("Current Position")
    st.map(
        {
            "lat": [flight["latitude"]],
            "lon": [flight["longitude"]],
        },
        zoom=4,
        use_container_width=True,
    )

with st.expander("ESP32-ready compact payload", expanded=True):
    esp_payload = build_esp32_payload(flight)
    st.code(json.dumps(esp_payload, indent=2), language="json")
    st.download_button(
        "Download payload JSON",
        data=json.dumps(esp_payload, separators=(",", ":")),
        file_name=f"{flight.get('ident', ident)}_esp32.json",
        mime="application/json",
    )

with st.expander("Normalized full flight data"):
    st.json(flight)

if auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
