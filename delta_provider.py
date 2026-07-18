from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from flight_utils import calculate_delay_minutes, normalize_delta_flight_number


class DeltaLookupError(RuntimeError):
    pass


class DeltaFlightStatusClient:
    """
    Loads Delta's full flight-details URL.

    Delta route format:
        /flightstatus/1/{flight_number}/{YYYY-MM-DD}/w

    Example:
        https://www.delta.com/flightstatus/1/2738/2026-07-18/w
    """

    BASE_URL = "https://www.delta.com/flightstatus"

    def __init__(self, headless: bool = True, timeout: int = 40) -> None:
        self.headless = headless
        self.timeout = timeout

    def _new_driver(self):
        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ModuleNotFoundError as exc:
            raise DeltaLookupError(
                "Selenium is not installed in this deployment."
            ) from exc

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,1800")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        options.add_experimental_option(
            "excludeSwitches",
            ["enable-automation", "enable-logging"],
        )
        options.add_experimental_option("useAutomationExtension", False)

        for binary in (
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ):
            if Path(binary).exists():
                options.binary_location = binary
                break

        driver_path = next(
            (
                path
                for path in (
                    "/usr/bin/chromedriver",
                    "/usr/lib/chromium/chromedriver",
                    "/usr/lib/chromium-browser/chromedriver",
                )
                if Path(path).exists()
            ),
            None,
        )

        try:
            service = Service(driver_path) if driver_path else Service()
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    """
                },
            )
            return driver
        except WebDriverException as exc:
            raise DeltaLookupError(
                "Chromium or ChromeDriver could not start."
            ) from exc

    @staticmethod
    def _body_text(driver) -> str:
        try:
            return driver.execute_script(
                "return document.body ? document.body.innerText : '';"
            ) or ""
        except Exception:
            return ""

    @staticmethod
    def _dismiss_banners(driver) -> None:
        try:
            driver.execute_script(
                """
                const terms = ['accept', 'agree', 'continue', 'allow all'];
                const elements = document.querySelectorAll(
                    'button, [role="button"]'
                );

                for (const element of elements) {
                    const text = (
                        element.innerText ||
                        element.getAttribute('aria-label') ||
                        ''
                    ).trim().toLowerCase();

                    if (terms.some(term => text.includes(term))) {
                        try {
                            element.click();
                            break;
                        } catch (error) {}
                    }
                }
                """
            )
        except Exception:
            pass

    def get_flight(self, flight_number: str, flight_date: date) -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        ident = normalize_delta_flight_number(flight_number)
        numeric_flight = ident.removeprefix("DL")
        date_text = flight_date.isoformat()

        url = (
            f"{self.BASE_URL}/1/"
            f"{quote(numeric_flight)}/"
            f"{quote(date_text)}/w"
        )

        driver = self._new_driver()

        try:
            driver.get(url)

            WebDriverWait(driver, self.timeout).until(
                lambda browser: browser.execute_script(
                    "return document.readyState"
                ) == "complete"
            )

            self._dismiss_banners(driver)

            # Wait for Delta's client-side status application to render more
            # than the static site header/footer.
            WebDriverWait(driver, self.timeout).until(
                lambda browser: (
                    numeric_flight in self._body_text(browser)
                    or ident in self._body_text(browser)
                    or "no flight" in self._body_text(browser).lower()
                    or "not found" in self._body_text(browser).lower()
                )
            )

            time.sleep(3)
            body_text = self._body_text(driver)
            lower = body_text.lower()

            if any(
                phrase in lower
                for phrase in (
                    "access denied",
                    "verify you are human",
                    "unable to process your request",
                    "temporarily unavailable",
                )
            ):
                raise DeltaLookupError(
                    "Delta blocked or rejected the hosted browser request."
                )

            if any(
                phrase in lower
                for phrase in (
                    "no flight found",
                    "flight not found",
                    "unable to locate",
                )
            ):
                raise DeltaLookupError(
                    f"Delta did not find {ident} for {flight_date:%B %d, %Y}."
                )

            if numeric_flight not in body_text and ident not in body_text:
                raise DeltaLookupError(
                    "Delta loaded the full details URL, but the rendered page "
                    "still did not contain the requested flight number. "
                    f"Loaded URL: {driver.current_url}. "
                    f"Page preview: {body_text[:900]}"
                )

            return self._parse_page_text(
                body_text=body_text,
                ident=ident,
                flight_date=flight_date,
                source_url=driver.current_url,
            )
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
        ident: str,
        flight_date: date,
        source_url: str,
    ) -> dict:
        text = re.sub(r"[ \t]+", " ", body_text)
        codes = self._airport_codes(text)

        origin_code = self._match(
            text,
            (
                r"(?:From|Depart(?:ure|ing)?)\s*[:\n ]+\s*([A-Z]{3})\b",
                r"\b([A-Z]{3})\b\s*(?:to|→|-)\s*[A-Z]{3}\b",
            ),
        )
        destination_code = self._match(
            text,
            (
                r"(?:To|Arriv(?:al|ing)?)\s*[:\n ]+\s*([A-Z]{3})\b",
                r"\b[A-Z]{3}\b\s*(?:to|→|-)\s*([A-Z]{3})\b",
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

        scheduled_departure = self._labeled_time(
            text,
            ("scheduled departure", "scheduled departs", "scheduled"),
        )
        estimated_departure = self._labeled_time(
            text,
            ("estimated departure", "estimated departs"),
        )
        actual_departure = self._labeled_time(
            text,
            ("actual departure", "departed"),
        )
        scheduled_arrival = self._labeled_time(
            text,
            ("scheduled arrival", "scheduled arrives"),
        )
        estimated_arrival = self._labeled_time(
            text,
            ("estimated arrival", "estimated arrives"),
        )
        actual_arrival = self._labeled_time(
            text,
            ("actual arrival", "arrived"),
        )

        all_times = re.findall(
            r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM)\b",
            text,
            re.I,
        )

        if not scheduled_departure and all_times:
            scheduled_departure = all_times[0]
        if not scheduled_arrival and len(all_times) > 1:
            scheduled_arrival = all_times[-1]

        gates = re.findall(
            r"\bGate\s*[:\n ]+\s*([A-Z]?\d+[A-Z]?)\b",
            text,
            re.I,
        )

        dep_sched = self._combine_time(flight_date, scheduled_departure)
        dep_est = self._combine_time(flight_date, estimated_departure)
        dep_actual = self._combine_time(flight_date, actual_departure)
        arr_sched = self._combine_time(flight_date, scheduled_arrival)
        arr_est = self._combine_time(flight_date, estimated_arrival)
        arr_actual = self._combine_time(flight_date, actual_arrival)

        return {
            "ident": ident,
            "status": status.title(),
            "cancelled": status.lower() in {"cancelled", "canceled"},
            "diverted": status.lower() == "diverted",
            "origin": {
                "code": origin_code or "—",
                "name": "",
                "city": "",
                "terminal": self._match(
                    text,
                    (
                        r"Departure Terminal\s*[:\n ]+\s*([A-Z0-9-]+)",
                        r"Terminal\s*[:\n ]+\s*([A-Z0-9-]+)",
                    ),
                ),
                "gate": gates[0] if gates else None,
                "scheduled": dep_sched,
                "estimated": dep_est,
                "actual": dep_actual,
                "delay_minutes": calculate_delay_minutes(
                    dep_actual or dep_est,
                    dep_sched,
                ),
            },
            "destination": {
                "code": destination_code or "—",
                "name": "",
                "city": "",
                "terminal": self._match(
                    text,
                    (
                        r"Arrival Terminal\s*[:\n ]+\s*([A-Z0-9-]+)",
                    ),
                ),
                "gate": gates[1] if len(gates) > 1 else None,
                "baggage_claim": self._match(
                    text,
                    (
                        r"(?:Baggage Claim|Carousel)"
                        r"\s*[:\n ]+\s*([A-Z0-9-]+)",
                    ),
                ),
                "scheduled": arr_sched,
                "estimated": arr_est,
                "actual": arr_actual,
                "delay_minutes": calculate_delay_minutes(
                    arr_actual or arr_est,
                    arr_sched,
                ),
            },
            "aircraft_type": self._match(
                text,
                (
                    r"(?:Aircraft|Equipment)"
                    r"\s*[:\n ]+\s*([A-Z0-9 -]{2,40})",
                ),
            ),
            "registration": None,
            "scheduled_minutes": self._duration_minutes(
                dep_sched,
                arr_sched,
            ),
            "flight_minutes": self._duration_minutes(
                dep_actual or dep_est,
                arr_actual or arr_est,
            ),
            "minutes_remaining": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "raw_source": "delta.com",
            "source_url": source_url,
        }

    @staticmethod
    def _labeled_time(text: str, labels: Iterable[str]) -> str | None:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}[^0-9]{{0,35}}"
                r"((?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM))",
                text,
                re.I,
            )
            if match:
                return match.group(1).upper()

        return None

    @staticmethod
    def _combine_time(day: date, value: str | None) -> str | None:
        if not value:
            return None

        for fmt in ("%I:%M %p", "%I:%M%p"):
            try:
                parsed = datetime.strptime(value.upper().strip(), fmt)
                return datetime.combine(day, parsed.time()).isoformat()
            except ValueError:
                continue

        return None

    @staticmethod
    def _duration_minutes(
        start: str | None,
        end: str | None,
    ) -> int | None:
        if not start or not end:
            return None

        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)

        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        return round((end_dt - start_dt).total_seconds() / 60)
