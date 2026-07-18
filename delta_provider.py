from __future__ import annotations

from datetime import date, datetime, timezone
import re
import time
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from flight_utils import calculate_delay_minutes, normalize_delta_flight_number


class DeltaLookupError(RuntimeError):
    pass


class DeltaFlightStatusClient:
    """
    Reads Delta's public flight-status page with a real browser.

    Delta does not document this as a supported public API. Selectors and page
    text may need adjustment when Delta changes the website.
    """

    URL = "https://www.delta.com/flightstatus/search"

    FLIGHT_INPUT_SELECTORS = (
        (By.CSS_SELECTOR, 'input[name*="flightNumber" i]'),
        (By.CSS_SELECTOR, 'input[id*="flightNumber" i]'),
        (By.CSS_SELECTOR, 'input[placeholder*="Flight Number" i]'),
        (By.XPATH, '//input[contains(translate(@aria-label, '
                   '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), '
                   '"flight number")]'),
    )

    DATE_INPUT_SELECTORS = (
        (By.CSS_SELECTOR, 'input[name*="date" i]'),
        (By.CSS_SELECTOR, 'input[id*="date" i]'),
        (By.CSS_SELECTOR, 'input[placeholder*="Date" i]'),
        (By.XPATH, '//input[contains(translate(@aria-label, '
                   '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), '
                   '"date")]'),
    )

    SEARCH_BUTTON_SELECTORS = (
        (By.CSS_SELECTOR, 'button[type="submit"]'),
        (By.XPATH, '//button[contains(translate(normalize-space(.), '
                   '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), '
                   '"search")]'),
        (By.XPATH, '//button[contains(translate(@aria-label, '
                   '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), '
                   '"search")]'),
    )

    def __init__(self, headless: bool = True, timeout: int = 30) -> None:
        self.headless = headless
        self.timeout = timeout

    def _new_driver(self) -> webdriver.Chrome:
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,1200")
        options.add_argument("--lang=en-US")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        options.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.notifications": 2,
                "intl.accept_languages": "en-US,en",
            },
        )

        try:
            return webdriver.Chrome(options=options)
        except WebDriverException as exc:
            raise DeltaLookupError(
                "Chrome could not start. Locally, install Google Chrome or "
                "Chromium. On Streamlit Community Cloud, keep `packages.txt` "
                "in the repository."
            ) from exc

    @staticmethod
    def _find_first(driver, selectors: Iterable[tuple[str, str]]):
        for by, selector in selectors:
            try:
                elements = driver.find_elements(by, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        return element
            except WebDriverException:
                continue
        return None

    @staticmethod
    def _dismiss_cookie_banner(driver) -> None:
        possible_buttons = (
            '//button[contains(translate(.,'
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
            '"accept")]',
            '//button[contains(translate(.,'
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
            '"agree")]',
            '//button[contains(translate(.,'
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
            '"continue")]',
        )

        for xpath in possible_buttons:
            try:
                buttons = driver.find_elements(By.XPATH, xpath)
                for button in buttons:
                    if button.is_displayed():
                        button.click()
                        time.sleep(0.5)
                        return
            except WebDriverException:
                pass

    def get_flight(self, flight_number: str, flight_date: date) -> dict:
        flight_number = normalize_delta_flight_number(flight_number)
        driver = self._new_driver()

        try:
            driver.get(self.URL)
            wait = WebDriverWait(driver, self.timeout)
            wait.until(lambda browser: browser.execute_script(
                "return document.readyState"
            ) == "complete")

            time.sleep(2)
            self._dismiss_cookie_banner(driver)

            flight_input = self._find_first(
                driver,
                self.FLIGHT_INPUT_SELECTORS,
            )
            if flight_input is None:
                raise DeltaLookupError(
                    "Delta's flight-number field could not be found."
                )

            flight_input.click()
            flight_input.send_keys(Keys.CONTROL, "a")
            flight_input.send_keys(flight_number.removeprefix("DL"))

            date_input = self._find_first(driver, self.DATE_INPUT_SELECTORS)
            if date_input is not None:
                date_input.click()
                date_input.send_keys(Keys.CONTROL, "a")
                # Delta has used both MM/DD/YYYY and localized date controls.
                date_input.send_keys(flight_date.strftime("%m/%d/%Y"))

            search_button = self._find_first(
                driver,
                self.SEARCH_BUTTON_SELECTORS,
            )
            if search_button is None:
                flight_input.send_keys(Keys.ENTER)
            else:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    search_button,
                )
                search_button.click()

            wait.until(
                lambda browser: (
                    flight_number in browser.find_element(By.TAG_NAME, "body").text
                    or "no flights" in browser.find_element(
                        By.TAG_NAME, "body"
                    ).text.lower()
                    or "not found" in browser.find_element(
                        By.TAG_NAME, "body"
                    ).text.lower()
                )
            )
            time.sleep(2)

            body_text = driver.find_element(By.TAG_NAME, "body").text
            if re.search(r"\b(no flights|not found|unable to locate)\b",
                         body_text, re.I):
                raise DeltaLookupError(
                    f"Delta did not return a flight for {flight_number} "
                    f"on {flight_date:%B %d, %Y}."
                )

            return self._parse_page_text(
                body_text=body_text,
                flight_number=flight_number,
                flight_date=flight_date,
            )

        except TimeoutException as exc:
            raise DeltaLookupError(
                "Delta's flight-status page did not finish loading. "
                "It may be blocking the hosted browser or the page may have changed."
            ) from exc
        finally:
            driver.quit()

    @staticmethod
    def _match(text: str, patterns: Iterable[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, re.I | re.M)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _airport_codes(text: str) -> list[str]:
        ignored = {
            "DEL", "ETA", "EST", "ACT", "AM", "PM", "DL",
            "USD", "FAQ", "SMS", "TSA",
        }
        result: list[str] = []
        for code in re.findall(r"(?<![A-Z])([A-Z]{3})(?![A-Z])", text):
            if code not in ignored and code not in result:
                result.append(code)
        return result

    def _parse_page_text(
        self,
        body_text: str,
        flight_number: str,
        flight_date: date,
    ) -> dict:
        """
        Convert Delta's rendered result text into a stable application object.

        The parser deliberately accepts several label arrangements because
        Delta periodically changes its visual layout.
        """
        text = re.sub(r"[ \t]+", " ", body_text)
        codes = self._airport_codes(text)

        origin_code = self._match(
            text,
            (
                r"(?:From|Depart(?:ure|ing)?)\s*[:\n ]+\s*([A-Z]{3})\b",
                r"\b([A-Z]{3})\b\s*(?:to|→)\s*[A-Z]{3}\b",
            ),
        )
        destination_code = self._match(
            text,
            (
                r"(?:To|Arriv(?:al|ing)?)\s*[:\n ]+\s*([A-Z]{3})\b",
                r"\b[A-Z]{3}\b\s*(?:to|→)\s*([A-Z]{3})\b",
            ),
        )

        if not origin_code and codes:
            origin_code = codes[0]
        if not destination_code and len(codes) > 1:
            destination_code = codes[1]

        status = self._match(
            text,
            (
                r"(?:Flight Status|Status)\s*[:\n ]+\s*"
                r"(On Time|Delayed|Cancelled|Canceled|Boarding|"
                r"Departed|En Route|Arrived|Landed|Diverted)",
                r"\b(On Time|Delayed|Cancelled|Canceled|Boarding|"
                r"Departed|En Route|Arrived|Landed|Diverted)\b",
            ),
        ) or "Unknown"

        times = re.findall(
            r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM)\b",
            text,
            re.I,
        )

        scheduled_departure = self._labeled_time(
            text, ("scheduled departure", "scheduled departs", "scheduled")
        )
        estimated_departure = self._labeled_time(
            text, ("estimated departure", "estimated departs", "estimated")
        )
        actual_departure = self._labeled_time(
            text, ("actual departure", "departed")
        )

        scheduled_arrival = self._labeled_time(
            text, ("scheduled arrival", "scheduled arrives")
        )
        estimated_arrival = self._labeled_time(
            text, ("estimated arrival", "estimated arrives")
        )
        actual_arrival = self._labeled_time(
            text, ("actual arrival", "arrived")
        )

        # Last-resort ordering for page variants that visually group times but
        # do not expose labels in body text.
        if not scheduled_departure and times:
            scheduled_departure = times[0]
        if not scheduled_arrival and len(times) > 1:
            scheduled_arrival = times[-1]

        dep_terminal = self._match(
            text,
            (
                r"(?:Departure Terminal|Terminal)\s*[:\n ]+\s*([A-Z0-9-]+)",
            ),
        )
        arr_terminal = self._match(
            text,
            (
                r"(?:Arrival Terminal)\s*[:\n ]+\s*([A-Z0-9-]+)",
            ),
        )
        gates = re.findall(
            r"(?:Gate)\s*[:\n ]+\s*([A-Z]?\d+[A-Z]?)\b",
            text,
            re.I,
        )
        dep_gate = gates[0] if gates else None
        arr_gate = gates[1] if len(gates) > 1 else None

        baggage = self._match(
            text,
            (
                r"(?:Baggage Claim|Carousel)\s*[:\n ]+\s*([A-Z0-9-]+)",
            ),
        )
        aircraft = self._match(
            text,
            (
                r"(?:Aircraft|Equipment)\s*[:\n ]+\s*([A-Z0-9 -]{2,30})",
            ),
        )

        dep_scheduled_iso = self._combine_time(
            flight_date, scheduled_departure
        )
        dep_estimated_iso = self._combine_time(
            flight_date, estimated_departure
        )
        dep_actual_iso = self._combine_time(
            flight_date, actual_departure
        )

        arr_scheduled_iso = self._combine_time(
            flight_date, scheduled_arrival
        )
        arr_estimated_iso = self._combine_time(
            flight_date, estimated_arrival
        )
        arr_actual_iso = self._combine_time(
            flight_date, actual_arrival
        )

        return {
            "ident": flight_number,
            "status": status.title(),
            "cancelled": status.lower() in {"cancelled", "canceled"},
            "diverted": status.lower() == "diverted",
            "origin": {
                "code": origin_code or "—",
                "name": "",
                "city": "",
                "terminal": dep_terminal,
                "gate": dep_gate,
                "scheduled": dep_scheduled_iso,
                "estimated": dep_estimated_iso,
                "actual": dep_actual_iso,
                "delay_minutes": calculate_delay_minutes(
                    dep_actual_iso or dep_estimated_iso,
                    dep_scheduled_iso,
                ),
            },
            "destination": {
                "code": destination_code or "—",
                "name": "",
                "city": "",
                "terminal": arr_terminal,
                "gate": arr_gate,
                "baggage_claim": baggage,
                "scheduled": arr_scheduled_iso,
                "estimated": arr_estimated_iso,
                "actual": arr_actual_iso,
                "delay_minutes": calculate_delay_minutes(
                    arr_actual_iso or arr_estimated_iso,
                    arr_scheduled_iso,
                ),
            },
            "aircraft_type": aircraft,
            "registration": None,
            "scheduled_minutes": self._duration_minutes(
                dep_scheduled_iso, arr_scheduled_iso
            ),
            "flight_minutes": self._duration_minutes(
                dep_actual_iso or dep_estimated_iso,
                arr_actual_iso or arr_estimated_iso,
            ),
            "minutes_remaining": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "raw_source": "delta.com",
        }

    @staticmethod
    def _labeled_time(text: str, labels: Iterable[str]) -> str | None:
        for label in labels:
            pattern = (
                rf"{re.escape(label)}"
                r"[^0-9]{0,30}"
                r"((?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM))"
            )
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).upper()
        return None

    @staticmethod
    def _combine_time(day: date, value: str | None) -> str | None:
        if not value:
            return None
        for fmt in ("%I:%M %p", "%I:%M%p"):
            try:
                parsed = datetime.strptime(value.upper().replace("  ", " "), fmt)
                return datetime.combine(day, parsed.time()).isoformat()
            except ValueError:
                continue
        return None

    @staticmethod
    def _duration_minutes(start: str | None, end: str | None) -> int | None:
        if not start or not end:
            return None
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            if end_dt < start_dt:
                from datetime import timedelta
                end_dt += timedelta(days=1)
            return round((end_dt - start_dt).total_seconds() / 60)
        except ValueError:
            return None
