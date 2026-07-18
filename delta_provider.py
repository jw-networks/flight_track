from __future__ import annotations

from datetime import date, datetime, timezone
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

from flight_utils import calculate_delay_minutes, normalize_delta_flight_number


class DeltaLookupError(RuntimeError):
    pass


class DeltaFlightStatusClient:
    """
    Reads Delta's public flight-status page through Chromium.

    Delta does not expose this web interface as a supported public API.
    Delta may return a bot-protection/interstitial page to cloud-hosted browsers.
    """

    URLS = (
        "https://www.delta.com/flightstatus/search",
        "https://www.delta.com/flightstatus",
    )

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 35,
        diagnostics_dir: str = "/tmp/delta_diagnostics",
    ) -> None:
        self.headless = headless
        self.timeout = timeout
        self.diagnostics_dir = Path(diagnostics_dir)

    def _new_driver(self):
        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ModuleNotFoundError as exc:
            raise DeltaLookupError(
                "Selenium is not installed. Confirm requirements.txt contains selenium."
            ) from exc

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,1400")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-features=IsolateOrigins,site-per-process")
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

        driver_paths = (
            "/usr/bin/chromedriver",
            "/usr/lib/chromium/chromedriver",
            "/usr/lib/chromium-browser/chromedriver",
        )

        try:
            service_path = next(
                (path for path in driver_paths if Path(path).exists()),
                None,
            )
            service = Service(service_path) if service_path else Service()
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en']
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    """
                },
            )
            return driver
        except WebDriverException as exc:
            raise DeltaLookupError(
                "Chromium could not start. Confirm packages.txt contains "
                "'chromium' and 'chromium-driver'."
            ) from exc

    def _save_diagnostics(self, driver, label: str) -> dict[str, str]:
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot = self.diagnostics_dir / f"{label}_{stamp}.png"
        html = self.diagnostics_dir / f"{label}_{stamp}.html"
        metadata = self.diagnostics_dir / f"{label}_{stamp}.json"

        try:
            driver.save_screenshot(str(screenshot))
        except Exception:
            pass

        try:
            html.write_text(driver.page_source, encoding="utf-8")
        except Exception:
            pass

        details = {
            "url": driver.current_url,
            "title": driver.title,
            "body_preview": self._body_text(driver)[:1500],
            "screenshot": str(screenshot),
            "html": str(html),
        }
        try:
            metadata.write_text(json.dumps(details, indent=2), encoding="utf-8")
        except Exception:
            pass
        return details

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
        script = """
        const terms = ['accept', 'agree', 'continue', 'allow all'];
        const all = [...document.querySelectorAll('button, [role="button"]')];
        for (const el of all) {
          const text = (el.innerText || el.getAttribute('aria-label') || '')
            .trim().toLowerCase();
          if (terms.some(t => text.includes(t))) {
            try { el.click(); return text; } catch (e) {}
          }
        }
        return null;
        """
        try:
            driver.execute_script(script)
        except Exception:
            pass

    @staticmethod
    def _find_input_in_document(driver) -> dict[str, Any] | None:
        """
        Recursively searches the main document and open shadow roots.

        Returns a JavaScript element reference plus basic metadata.
        """
        script = r"""
        function collect(root, output) {
          const candidates = root.querySelectorAll(
            'input, delta-input, [contenteditable="true"], [role="textbox"]'
          );

          for (const el of candidates) {
            const attrs = [
              el.id,
              el.name,
              el.placeholder,
              el.getAttribute && el.getAttribute('aria-label'),
              el.getAttribute && el.getAttribute('data-testid'),
              el.getAttribute && el.getAttribute('autocomplete')
            ].filter(Boolean).join(' ').toLowerCase();

            const visible = !!(
              el.offsetWidth || el.offsetHeight || el.getClientRects().length
            );

            if (
              visible &&
              (
                attrs.includes('flight number') ||
                attrs.includes('flightnumber') ||
                attrs.includes('flight-number') ||
                attrs.includes('flight_number')
              )
            ) {
              return {
                element: el,
                attrs: attrs,
                tag: el.tagName,
                type: el.type || ''
              };
            }
          }

          const every = root.querySelectorAll('*');
          for (const el of every) {
            if (el.shadowRoot) {
              const found = collect(el.shadowRoot, output);
              if (found) return found;
            }
          }
          return null;
        }
        return collect(document, []);
        """
        try:
            return driver.execute_script(script)
        except Exception:
            return None

    @staticmethod
    def _find_date_input(driver):
        script = r"""
        function search(root) {
          const candidates = root.querySelectorAll(
            'input, [contenteditable="true"], [role="textbox"]'
          );
          for (const el of candidates) {
            const attrs = [
              el.id, el.name, el.placeholder,
              el.getAttribute && el.getAttribute('aria-label'),
              el.getAttribute && el.getAttribute('data-testid')
            ].filter(Boolean).join(' ').toLowerCase();

            const visible = !!(
              el.offsetWidth || el.offsetHeight || el.getClientRects().length
            );
            if (
              visible &&
              (
                attrs.includes('date') ||
                attrs.includes('departure day') ||
                attrs.includes('travel day')
              )
            ) return el;
          }
          for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) {
              const found = search(el.shadowRoot);
              if (found) return found;
            }
          }
          return null;
        }
        return search(document);
        """
        try:
            return driver.execute_script(script)
        except Exception:
            return None

    @staticmethod
    def _find_search_button(driver):
        script = r"""
        function search(root) {
          for (const el of root.querySelectorAll(
            'button, input[type="submit"], [role="button"]'
          )) {
            const text = [
              el.innerText,
              el.value,
              el.getAttribute && el.getAttribute('aria-label'),
              el.getAttribute && el.getAttribute('data-testid')
            ].filter(Boolean).join(' ').trim().toLowerCase();

            const visible = !!(
              el.offsetWidth || el.offsetHeight || el.getClientRects().length
            );

            if (
              visible &&
              (
                text === 'search' ||
                text.includes('search flight') ||
                text.includes('check status') ||
                text.includes('find flight')
              )
            ) return el;
          }
          for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) {
              const found = search(el.shadowRoot);
              if (found) return found;
            }
          }
          return null;
        }
        return search(document);
        """
        try:
            return driver.execute_script(script)
        except Exception:
            return None

    @staticmethod
    def _set_value(driver, element, value: str) -> None:
        driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            const prototype =
              el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(
              prototype, 'value'
            )?.set;
            if (setter) setter.call(el, value);
            else el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('blur', {bubbles: true}));
            """,
            element,
            value,
        )

    @staticmethod
    def _looks_blocked(driver) -> bool:
        text = DeltaFlightStatusClient._body_text(driver).lower()
        title = (driver.title or "").lower()
        source = (driver.page_source or "").lower()

        indicators = (
            "access denied",
            "verify you are human",
            "checking your browser",
            "security check",
            "unusual traffic",
            "temporarily unavailable",
            "akamai",
            "perimeterx",
            "captcha",
            "plane.gif",
        )
        return (
            len(text.strip()) < 80
            or any(value in text for value in indicators)
            or any(value in title for value in indicators)
            or ("plane.gif" in source and len(text.strip()) < 500)
        )

    def _switch_to_frame_with_input(self, driver):
        """
        Searches the main document and all accessible iframes.
        """
        from selenium.webdriver.common.by import By

        driver.switch_to.default_content()
        result = self._find_input_in_document(driver)
        if result:
            return result

        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for frame in frames:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                result = self._find_input_in_document(driver)
                if result:
                    return result
            except Exception:
                continue

        driver.switch_to.default_content()
        return None

    def get_flight(self, flight_number: str, flight_date: date) -> dict:
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.support.ui import WebDriverWait

        flight_number = normalize_delta_flight_number(flight_number)
        driver = self._new_driver()

        try:
            loaded_url = None
            for url in self.URLS:
                driver.get(url)
                WebDriverWait(driver, self.timeout).until(
                    lambda browser: browser.execute_script(
                        "return document.readyState"
                    ) == "complete"
                )
                time.sleep(5)
                self._dismiss_banners(driver)

                if not self._looks_blocked(driver):
                    loaded_url = url
                    break

            if loaded_url is None:
                details = self._save_diagnostics(driver, "delta_blocked")
                raise DeltaLookupError(
                    "Delta did not serve the interactive flight-status page to "
                    "this Streamlit Cloud instance. It returned a blank, loading, "
                    "or bot-protection page instead. This cannot be repaired with "
                    "a different CSS selector. Diagnostic preview: "
                    f"{details.get('body_preview') or '[empty body]'}"
                )

            flight_field = self._switch_to_frame_with_input(driver)
            if not flight_field:
                details = self._save_diagnostics(driver, "field_not_found")
                raise DeltaLookupError(
                    "Delta loaded a page, but no flight-number control was exposed "
                    "in the document, accessible iframes, or open shadow roots. "
                    f"Page title: {details.get('title')!r}; "
                    f"body preview: {details.get('body_preview') or '[empty body]'}"
                )

            element = flight_field["element"]
            self._set_value(
                driver,
                element,
                flight_number.removeprefix("DL"),
            )

            date_field = self._find_date_input(driver)
            if date_field is not None:
                self._set_value(
                    driver,
                    date_field,
                    flight_date.strftime("%m/%d/%Y"),
                )

            button = self._find_search_button(driver)
            if button is not None:
                driver.execute_script("arguments[0].click();", button)
            else:
                driver.execute_script(
                    """
                    const el = arguments[0];
                    el.dispatchEvent(
                      new KeyboardEvent('keydown', {
                        key:'Enter', code:'Enter', keyCode:13,
                        which:13, bubbles:true
                      })
                    );
                    """,
                    element,
                )

            time.sleep(6)
            body_text = self._body_text(driver)

            if re.search(
                r"\b(no flights|not found|unable to locate|try again)\b",
                body_text,
                re.I,
            ):
                raise DeltaLookupError(
                    f"Delta did not return flight {flight_number} for "
                    f"{flight_date:%B %d, %Y}."
                )

            if flight_number not in body_text and flight_number[2:] not in body_text:
                details = self._save_diagnostics(driver, "results_missing")
                raise DeltaLookupError(
                    "Delta accepted the form interaction, but no recognizable "
                    "flight result appeared. Body preview: "
                    f"{details.get('body_preview') or '[empty body]'}"
                )

            return self._parse_page_text(
                body_text,
                flight_number,
                flight_date,
            )

        except TimeoutException as exc:
            self._save_diagnostics(driver, "timeout")
            raise DeltaLookupError(
                "Delta's page timed out before becoming usable."
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

        if not scheduled_departure and times:
            scheduled_departure = times[0]
        if not scheduled_arrival and len(times) > 1:
            scheduled_arrival = times[-1]

        gates = re.findall(
            r"(?:Gate)\s*[:\n ]+\s*([A-Z]?\d+[A-Z]?)\b",
            text,
            re.I,
        )

        dep_scheduled_iso = self._combine_time(flight_date, scheduled_departure)
        dep_estimated_iso = self._combine_time(flight_date, estimated_departure)
        dep_actual_iso = self._combine_time(flight_date, actual_departure)
        arr_scheduled_iso = self._combine_time(flight_date, scheduled_arrival)
        arr_estimated_iso = self._combine_time(flight_date, estimated_arrival)
        arr_actual_iso = self._combine_time(flight_date, actual_arrival)

        return {
            "ident": flight_number,
            "status": status.title(),
            "cancelled": status.lower() in {"cancelled", "canceled"},
            "diverted": status.lower() == "diverted",
            "origin": {
                "code": origin_code or "—",
                "name": "",
                "city": "",
                "terminal": self._match(
                    text,
                    (r"Departure Terminal\s*[:\n ]+\s*([A-Z0-9-]+)",),
                ),
                "gate": gates[0] if gates else None,
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
                "terminal": self._match(
                    text,
                    (r"Arrival Terminal\s*[:\n ]+\s*([A-Z0-9-]+)",),
                ),
                "gate": gates[1] if len(gates) > 1 else None,
                "baggage_claim": self._match(
                    text,
                    (
                        r"(?:Baggage Claim|Carousel)"
                        r"\s*[:\n ]+\s*([A-Z0-9-]+)",
                    ),
                ),
                "scheduled": arr_scheduled_iso,
                "estimated": arr_estimated_iso,
                "actual": arr_actual_iso,
                "delay_minutes": calculate_delay_minutes(
                    arr_actual_iso or arr_estimated_iso,
                    arr_scheduled_iso,
                ),
            },
            "aircraft_type": self._match(
                text,
                (
                    r"(?:Aircraft|Equipment)"
                    r"\s*[:\n ]+\s*([A-Z0-9 -]{2,30})",
                ),
            ),
            "registration": None,
            "scheduled_minutes": self._duration_minutes(
                dep_scheduled_iso,
                arr_scheduled_iso,
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
            match = re.search(
                rf"{re.escape(label)}[^0-9]{{0,30}}"
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
    def _duration_minutes(start: str | None, end: str | None) -> int | None:
        if not start or not end:
            return None
        try:
            from datetime import timedelta

            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            return round((end_dt - start_dt).total_seconds() / 60)
        except ValueError:
            return None
