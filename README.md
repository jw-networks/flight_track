# Delta Flight Tracker

This Streamlit application reads Delta's public flight-status details page
through Selenium and Chromium.

## Repository layout

- `app.py`
- `delta_provider.py`
- `flight_utils.py`
- `requirements.txt`
- `packages.txt`
- `.streamlit/config.toml`

## Streamlit Community Cloud

Deploy `app.py` as the application entrypoint.

The provider builds URLs in this form:

`https://www.delta.com/flightstatus/1/{flight}/{YYYY-MM-DD}/w`

Example:

`https://www.delta.com/flightstatus/1/2738/2026-07-18/w`

## Important limitation

Delta's page loads flight details through client-side JavaScript. Delta may block
or fail to serve that background request to Streamlit Cloud. The application now
handles that condition as a visible error instead of exposing a raw Selenium
traceback.
