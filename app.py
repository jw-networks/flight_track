from __future__ import annotations

from datetime import date
import json
import time

import streamlit as st

from delta_provider import DeltaFlightStatusClient, DeltaLookupError
from flight_utils import (
    build_esp32_payload,
    demo_flight,
    format_clock,
    format_duration,
    normalize_delta_flight_number,
    progress_percent,
)

st.set_page_config(
    page_title="Delta Flight Dashboard",
    page_icon="✈️",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
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
        background: linear-gradient(120deg, #710019, #b0002b);
        color: white;
      }
      .flight-banner h2 {margin: 0;}
      .flight-banner p {margin: 5px 0 0; opacity: .9;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60, show_spinner=False)
def load_delta_flight(flight_number: str, flight_date: date) -> dict:
    client = DeltaFlightStatusClient(headless=True)
    return client.get_flight(flight_number, flight_date)


def show_airport(title: str, airport: dict) -> None:
    st.subheader(title)
    st.markdown(
        f"### {airport.get('code') or '—'}  \n"
        f"{airport.get('name') or ''}"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Scheduled", format_clock(airport.get("scheduled")))
    c2.metric("Estimated", format_clock(airport.get("estimated")))
    c3.metric("Actual", format_clock(airport.get("actual")))

    c4, c5, c6 = st.columns(3)
    c4.metric("Terminal", airport.get("terminal") or "—")
    c5.metric("Gate", airport.get("gate") or "—")
    c6.metric("Delay", f"{airport.get('delay_minutes', 0)} min")


with st.sidebar:
    st.title("Flight Search")

    with st.form("flight_search"):
        raw_number = st.text_input(
            "Delta flight number",
            value=st.session_state.get("raw_number", "DL1234"),
            placeholder="DL1234 or 1234",
        )
        selected_date = st.date_input(
            "Flight date",
            value=st.session_state.get("selected_date", date.today()),
        )
        demo_mode = st.toggle(
            "Demo mode",
            value=st.session_state.get("demo_mode", True),
            help="Uses sample data instead of loading Delta.com.",
        )
        submitted = st.form_submit_button(
            "Load flight",
            type="primary",
            use_container_width=True,
        )

    st.divider()
    auto_refresh = st.toggle("Auto-refresh", value=False)
    refresh_seconds = st.select_slider(
        "Refresh interval",
        options=[60, 120, 300, 600],
        value=120,
        format_func=lambda seconds: f"{seconds} sec",
        disabled=not auto_refresh,
    )

if submitted:
    st.session_state.raw_number = raw_number
    st.session_state.selected_date = selected_date
    st.session_state.demo_mode = demo_mode
    st.session_state.load_requested = True
    load_delta_flight.clear()

st.title("Delta Flight Dashboard")
st.caption("Flight information loaded directly from Delta.com.")

if not st.session_state.get("load_requested"):
    st.info("Enter a Delta flight number and select **Load flight**.")
    st.stop()

try:
    flight_number = normalize_delta_flight_number(st.session_state.raw_number)

    with st.spinner(f"Checking Delta flight {flight_number}…"):
        if st.session_state.demo_mode:
            flight = demo_flight(flight_number, st.session_state.selected_date)
        else:
            flight = load_delta_flight(
                flight_number,
                st.session_state.selected_date,
            )
except (ValueError, DeltaLookupError) as exc:
    st.error(str(exc))
    st.caption(
        "Delta does not publish this web interface as a supported public API. "
        "A Delta.com layout or bot-protection change may require updating "
        "`delta_provider.py`."
    )
    st.stop()

origin = flight["origin"]
destination = flight["destination"]
progress = progress_percent(flight)

st.markdown(
    f"""
    <div class="flight-banner">
      <h2>{flight.get('ident', flight_number)} ·
      {origin.get('code', '—')} → {destination.get('code', '—')}</h2>
      <p>{flight.get('status', 'Unknown')} ·
      Updated {format_clock(flight.get('updated_at'), include_zone=True)}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Status", flight.get("status") or "Unknown")
m2.metric("Progress", f"{progress}%")
m3.metric("Arrival delay", f"{destination.get('delay_minutes', 0)} min")
m4.metric("Aircraft", flight.get("aircraft_type") or "—")
m5.metric("Baggage", destination.get("baggage_claim") or "—")

st.progress(progress / 100, text=f"{progress}% complete")

left, right = st.columns(2)
with left:
    show_airport("Departure", origin)
with right:
    show_airport("Arrival", destination)

st.divider()
st.subheader("Additional Details")

d1, d2, d3, d4 = st.columns(4)
d1.metric("Scheduled duration", format_duration(flight.get("scheduled_minutes")))
d2.metric("Estimated duration", format_duration(flight.get("flight_minutes")))
d3.metric("Departure city", origin.get("city") or "—")
d4.metric("Arrival city", destination.get("city") or "—")

with st.expander("ESP32-ready payload", expanded=True):
    payload = build_esp32_payload(flight)
    st.code(json.dumps(payload, indent=2), language="json")
    st.download_button(
        "Download JSON",
        json.dumps(payload, separators=(",", ":")),
        file_name=f"{flight_number}_display.json",
        mime="application/json",
    )

with st.expander("Normalized Delta data"):
    st.json(flight)

if auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
