# Delta Flight Dashboard

A Streamlit dashboard for looking up a Delta flight by flight number and date.
It includes a demo mode and an ESP32-oriented compact JSON payload.

## Features

- Delta flight-number normalization (`1234`, `DL1234`, or `DAL1234`)
- Scheduled, estimated, and actual departure/arrival
- Gates, terminals, delays, aircraft, altitude, speed, and progress
- Auto-refresh
- Demo mode that works without an API key
- Compact JSON payload designed for a future ESP32 display
- Flight-provider code isolated from the user interface

## Install

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Run immediately in demo mode

```bash
streamlit run app.py
```

Open the address Streamlit prints, normally `http://localhost:8501`.

## Enable live FlightAware data

1. Obtain a FlightAware AeroAPI key.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`.
3. Replace the placeholder with the real key.
4. Restart Streamlit.
5. Disable **Demo mode** in the sidebar.

You may instead define the environment variable:

```text
FLIGHTAWARE_API_KEY=your-key
```

## ESP32 plan

The Streamlit app currently exposes the compact payload in the UI and lets you
download it. The next implementation step is to add a tiny HTTP endpoint, for
example:

```text
GET /display/DL1234
```

The ESP32 can poll that endpoint every 30–120 seconds and render only the fields
it needs. The API key should remain on the computer/server and never be stored
on the ESP32.

Example compact payload:

```json
{
  "v": 1,
  "flight": "DL1234",
  "from": "ATL",
  "to": "MCO",
  "status": "En Route",
  "gate_out": "B18",
  "gate_in": "72",
  "delay": 7,
  "progress": 64,
  "alt_ft": 35000,
  "speed_kt": 462,
  "remaining_min": 42
}
```

## Notes

FlightAware fields can vary by flight status, coverage, and subscription tier.
The normalizer intentionally tolerates missing live-position and aircraft data.
