# Delta Flight Dashboard

A Streamlit dashboard that reads flight information from Delta's public
flight-status page using a headless Chromium browser.

## Important limitation

Delta does not publish this web interface as a supported public developer API.
The page selectors or anti-automation controls may change. The application
handles several page layouts, but `delta_provider.py` may eventually require an
update.

## Run locally

Python 3.10+ and Chrome/Chromium are recommended.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app starts in Demo mode. Turn Demo mode off to query Delta.com.

## Streamlit Community Cloud

Push all files to GitHub and deploy `app.py`.

`packages.txt` asks Streamlit Cloud to install Chromium and the Chromium driver.
No flight-data API key is required.

## ESP32

The Streamlit page generates a compact JSON object containing the flight,
airports, status, gates, terminals, times, delay, baggage claim, and progress.
A later API service can expose that same payload for an ESP32 to poll.
