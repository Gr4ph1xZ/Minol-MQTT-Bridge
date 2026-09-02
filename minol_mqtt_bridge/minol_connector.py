"""
Minol Customer Portal API Client

Handles authentication and data fetching from the Minol customer portal
using Playwright for Azure B2C SAML authentication and requests for API calls.
"""

import requests
from bs4 import BeautifulSoup
import json
import logging
import base64
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import time
from typing import Dict, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
}


def _dump_debug(filename, text, force=False):
    """
    Write a page/response body next to the add-on for troubleshooting.

    Only at DEBUG unless `force` (an actual failure worth keeping evidence of),
    so a normal run does not leave megabytes of HTML in the container, and
    never fatal - a read-only working directory must not take the sync down.
    """
    if not force and not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        logger.debug(f"Wrote {filename} for troubleshooting")
    except OSError as e:
        logger.debug(f"Could not write {filename}: {e}")


class MinolConnector:
    """Minol customer portal API client with Playwright-based authentication."""

    # consType values the portal accepts, per category, in the order they are
    # tried. Heating is keyed by the metering technology actually installed:
    # "HEIZUNG" for heat cost allocators reporting in allocator units, "HZKWH"
    # for heat meters reporting kWh (dlg "100KWH"). An account only ever has
    # one of them - the portal accepts the other but answers with an empty
    # table and a chart holding nothing but the DIN reference curve.
    #
    # "HEIZUNG" is tried first because it is what the portal's own UI5 app
    # (monitoring/Component.js) seeds its filter model with; that app then
    # replaces it with whatever the account actually has. Probing is the only
    # way to learn that from outside - no endpoint reports it.
    CONS_TYPES = {
        "heating": ("HEIZUNG", "HZKWH"),
        "hot_water": ("WARMWASSER",),
        "cold_water": ("KALTWASSER",),
    }

    # valuesInKWH to request per category. Water is asked for in its native m³:
    # with valuesInKWH=True the portal labels the warm water row "KWH" but
    # leaves 'consumption' in m³ (only 'consumptionBew' and the chart are
    # converted), so the row would be published as a volume wearing an energy
    # unit. Heating asks for kWh, which is what a heat meter reports anyway.
    VALUES_IN_KWH = {
        "heating": True,
        "hot_water": False,
        "cold_water": False,
    }

    # timelineStart for the consumption request. The portal has no notion of a
    # billing period here: it reports 'anfangsstand' as the meter reading at the
    # window start and 'consumption' as the difference to the window end, for
    # exactly the window asked for. A window that slides with the clock would
    # therefore yield a rolling sum that falls whenever a heavy month drops out
    # of it, and Home Assistant reads such a fall as a meter reset. Anchoring
    # the start far in the past makes the portal clamp to the earliest reading
    # it holds, so consumption is measured from a fixed point and only grows.
    TIMELINE_ANCHOR = "200001"

    def __init__(self, email: str, password: str, base_url: str = "https://webservices.minol.com"):
        """Initialize the connector with user credentials."""
        self.email = email
        self.password = password

        self.base_url = base_url
        self.login_url = f"{base_url}/"
        self.acs_url = f"{base_url}/saml2/sp/acs"

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        })

        self.user_tenants = None
        self.user_num = None
        self.csrf_token = None
        self._authenticated = False
        self._last_data: Optional[Dict] = None
        self._last_update: Optional[datetime] = None
        self._cache_duration = timedelta(hours=1)

    def login(self):
        """Perform Azure B2C SAML authentication using Playwright."""
        logger.info("Starting Playwright authentication...")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context()
            page = context.new_page()

            try:
                logger.info("Navigating to monitoring page...")
                monitoring_url = f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true&redirect2=true"
                page.goto(monitoring_url, wait_until="domcontentloaded")

                time.sleep(3)
                logger.info(f"Current URL: {page.url}")

                if "minolauth.b2clogin.com" in page.url:
                    logger.info("On Azure B2C login page")
                else:
                    logger.info("Checking for login redirect...")
                    _dump_debug("current_page.html", page.content())

                    try:
                        page.wait_for_url("**/minolauth.b2clogin.com/**", timeout=5000)
                    except Exception:
                        logger.warning("No redirect to Azure B2C detected")

                if "minolauth.b2clogin.com" in page.url:
                    logger.info("Filling login form...")
                    time.sleep(2)

                    email_input = page.locator('input[id="signInName"], input[name="signInName"], input[type="email"], input[placeholder*="Kundennummer"]')
                    email_input.wait_for(state="visible", timeout=10000)
                    email_input.fill(self.email)

                    password_input = page.locator('input[id="password"], input[name="password"], input[type="password"]')
                    password_input.wait_for(state="visible", timeout=5000)
                    password_input.fill(self.password)

                    sign_in_button = page.locator('button[type="submit"], button#next')
                    sign_in_button.click()

                    logger.info("Waiting for redirect...")
                    page.wait_for_url(f"{self.base_url}/**", timeout=30000)
                    time.sleep(2)

                    logger.info("Navigating to monitoring page...")
                    page.goto(monitoring_url, wait_until="networkidle")
                    time.sleep(3)
                else:
                    logger.info("Checking authentication status...")

                logger.info("Extracting cookies...")
                cookies = context.cookies()

                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie['name'],
                        value=cookie['value'],
                        domain=cookie.get('domain', ''),
                        path=cookie.get('path', '/'),
                        secure=cookie.get('secure', False)
                    )

                logger.info(f"Transferred {len(cookies)} cookies")

                mysapsso2_present = any(c['name'] == 'MYSAPSSO2' for c in cookies)
                if mysapsso2_present:
                    logger.info("MYSAPSSO2 cookie obtained")
                else:
                    logger.warning("MYSAPSSO2 cookie not found")

            except Exception as e:
                logger.error(f"Error during Playwright login: {e}")
                raise
            finally:
                browser.close()

        logger.info("Login successful.")
        self._authenticated = True


    def _get_monitoring_index(self):
        """Access the monitoring index page."""
        logger.info("Getting monitoring index page...")
        url = f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true"
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'de-DE,de;q=0.9,en-DE;q=0.8,en;q=0.7,en-US;q=0.6',
            'DNT': '1',
            'Referer': f'{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true/',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
        }
        try:
            response = self.session.get(url, headers=headers, allow_redirects=True)
            response.raise_for_status()
            with open("monitoring_index_page.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            logger.info(f"Monitoring index page response status code: {response.status_code}")
            logger.info(f"Monitoring index page response cookies: {response.cookies}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting monitoring index page: {e}")
            raise
    
    def _get_monitoring_client(self):
        """Complete the login process by accessing the monitoring client URL."""
        logger.info("Getting monitoring client...")
        url = f"{self.base_url}/irj/servlet/prt/portal/prtroot/pcd!3aportal_content!2fminol!2ff_PortalLayouts!2fv_monitoringClient"
        try:
            response = self.session.get(url, allow_redirects=True)
            response.raise_for_status()
            with open("monitoring_page_response.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            logger.info(f"Monitoring client response status code: {response.status_code}")
            logger.info(f"Monitoring client response cookies: {response.cookies}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting monitoring client: {e}")
            raise

    def get_user_tenants(self):
        """Fetch user tenants to extract the userNum."""
        logger.info("Fetching user tenants...")
        url = f"{self.base_url}/minol.com~kundenportal~em~web/rest/EMData/getUserTenants"
        headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/json; charset=utf-8',
            'Referer': f'{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true',
            'X-Requested-With': 'XMLHttpRequest'
        }
        logger.debug(f"Fetching user tenants from URL: {url}")
        logger.debug(f"Request Headers: {json.dumps(headers, indent=2)}")
        try:
            logger.debug(f"Session cookies before getUserTenants: {self.session.cookies}")
            response = self.session.get(url, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '')
            logger.debug(f"getUserTenants response URL: {response.url}")
            logger.debug(f"getUserTenants response headers: {dict(response.headers)}")
            if 'application/json' not in content_type:
                raise ValueError(f"Expected JSON response, but got Content-Type: {content_type}")

            self.user_tenants = response.json()
            logger.debug(f"User tenants response: {json.dumps(self.user_tenants, indent=2)}")
            if self.user_tenants and len(self.user_tenants) > 0:
                self.user_num = self.user_tenants[0].get("userNumber")
                logger.info(f"userNum found: {self.user_num}")
            else:
                raise ValueError("User tenants not found or empty.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching user tenants: {e}")
            raise
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error processing user tenants response: {e}")
            _dump_debug("user_tenants_error_response.html", response.text, force=True)
            logger.error("Response content saved to user_tenants_error_response.html")
            raise

    def fetch_em_data(self, timeline_start, timeline_end, cons_type="HEIZUNG",
                      dlg_key="100EH", values_in_kwh=True, max_retries=3):
        """
        Fetch eMonitoring data for a specific consumption type.

        Args:
            timeline_start (str): Start period in format YYYYMM (e.g., "202411")
            timeline_end (str): End period in format YYYYMM (e.g., "202510")
            cons_type (str): Consumption key the portal understands - "HEIZUNG",
                "HZKWH", "WARMWASSER", "KALTWASSER", "STROM" or "GAS". An
                unknown key is not rejected; the portal answers with every
                category at once. See MinolConnector.CONS_TYPES.
            dlg_key (str): Dialog key, default "100EH" for heating. The portal
                appears to ignore it - the response is selected by cons_type.
            values_in_kwh (bool): Ask the portal to convert values to kWh. It
                converts the chart and the row's consumptionBew, but leaves the
                row's 'consumption' in the meter's own unit while relabelling
                'unit' as KWH - so water must be requested with False to stay
                in m³. See MinolConnector.VALUES_IN_KWH.
            max_retries (int): Attempts for transient network/5xx/JSON errors.

        Returns:
            dict: JSON response containing table (per room) and chart (timeline) data
        """
        logger.info(f"Fetching eMonitoring data for {cons_type} from {timeline_start} "
                    f"to {timeline_end} (valuesInKWH={values_in_kwh})")
        url = f"{self.base_url}/minol.com~kundenportal~em~web/rest/EMData/readData"
        payload = {
            "userNum": self.user_num,
            "layer": "NE",
            "scale": "CALMONTH",
            "chartRefUnit": "ABS",
            "refObject": "DIN_AVG",
            "consType": cons_type,
            "dashBoardKey": "PE",
            "timelineStart": timeline_start,
            "timelineStartTxt": f"{timeline_start[4:]}.{timeline_start[:4]}",
            "timelineEnd": timeline_end,
            "timelineEndTxt": f"{timeline_end[4:]}.{timeline_end[:4]}",
            "valuesInKWH": values_in_kwh,
            "dlgKey": dlg_key,
        }
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=UTF-8",
            "Referer": f'{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true',
            "X-Requested-With": "XMLHttpRequest",
        }

        logger.debug(f"Fetching EM data from URL: {url}")
        logger.debug(f"Request Payload: {json.dumps(payload, indent=2)}")
        logger.debug(f"Request Headers: {json.dumps(headers, indent=2)}")

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.post(url, headers=headers, data=json.dumps(payload))
                response.raise_for_status()
                logger.debug(f"EM data response status: {response.status_code}")
                # Full body at DEBUG - the per-room 'table' can be far past the
                # first few hundred characters, and an empty table for HEIZUNG is
                # only diagnosable with the whole payload.
                logger.debug(f"EM data response content ({cons_type}): {response.text}")
                return response.json()
            except requests.exceptions.HTTPError as e:
                last_error = e
                status = getattr(e.response, "status_code", None)
                if status is not None and status < 500:
                    logger.error(f"HTTP {status} fetching EM data for {cons_type}: {e}")
                    raise
                logger.warning(f"HTTP {status} fetching EM data for {cons_type} "
                               f"(attempt {attempt}/{max_retries}): {e}")
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(f"Network error fetching EM data for {cons_type} "
                               f"(attempt {attempt}/{max_retries}): {e}")
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(f"Non-JSON EM data response for {cons_type} "
                               f"(attempt {attempt}/{max_retries}): {e}")
                logger.debug(f"Offending response body: {response.text[:2000]}")
            if attempt < max_retries:
                time.sleep(2 * attempt)

        logger.error(f"Giving up fetching EM data for {cons_type} after "
                     f"{max_retries} attempts: {last_error}")
        raise last_error or RuntimeError(f"EM data fetch failed for {cons_type}")

    def get_all_consumption_data(self, timeline_start, timeline_end, history_months=None):
        """
        Fetch all consumption data types (heating, hot water, cold water) organized by room.

        Args:
            timeline_start (str): Start period in format YYYYMM (e.g., "202411")
            timeline_end (str): End period in format YYYYMM (e.g., "202510")

        Returns:
            dict: Structured consumption data with the following format:
                {
                    "heating": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "hot_water": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "cold_water": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "timestamp": "ISO timestamp",
                    "period": {"start": "YYYYMM", "end": "YYYYMM"}
                }
        """
        from datetime import datetime

        logger.info(f"Fetching all consumption data from {timeline_start} to {timeline_end}")

        consumption_data = {
            "timestamp": datetime.now().isoformat(),
            "period": {
                "start": timeline_start,
                "end": timeline_end
            }
        }

        for key, dlg_key in (
            ("heating", "100EH"),
            ("hot_water", "100WW"),
            ("cold_water", "100KW"),
        ):
            try:
                processed = self._fetch_category(
                    key, dlg_key, timeline_start, timeline_end
                )
                # After the total is settled, so trimming only affects the
                # monthly breakdown and the DIN comparison.
                self._trim_timeline(processed, history_months)

                if not processed["by_room"] and processed.get("total_source") != "chart":
                    logger.warning(
                        f"{key}: portal returned no per-room table and no usable "
                        f"chart values for {timeline_start}-{timeline_end} "
                        f"(tried consType {', '.join(self.CONS_TYPES[key])})"
                    )

                consumption_data[key] = processed
            except Exception as e:
                logger.error(f"Error fetching {key} data: {e}")
                consumption_data[key] = {"error": str(e)}

        return consumption_data

    def _fetch_category(self, key, dlg_key, timeline_start, timeline_end):
        """
        Fetch one consumption category, trying each consType the portal may use.

        Returns the first candidate that actually carries values; if none do,
        the first well-formed (but empty) result is returned so that callers
        still see the DIN reference curve and can report the gap.
        """
        empty = None
        values_in_kwh = self.VALUES_IN_KWH[key]

        for cons_type in self.CONS_TYPES[key]:
            raw = self.fetch_em_data(
                timeline_start, timeline_end, cons_type=cons_type, dlg_key=dlg_key,
                values_in_kwh=values_in_kwh
            )

            if not self._cons_type_understood(raw, cons_type):
                logger.warning(
                    f"{key}: portal did not recognise consType={cons_type} and "
                    f"answered with every category at once; ignoring that response"
                )
                continue

            processed = self._process_consumption_data(
                raw, cons_type, timeline_start, timeline_end
            )

            if processed["by_room"] or any(e["value"] for e in processed["timeline"]):
                logger.info(f"{key}: using consType={cons_type} "
                            f"({len(processed['by_room'])} meter(s), "
                            f"total {processed['total_consumption']})")
                return processed

            # Expected: an account has meters under exactly one of the keys, so
            # the others are silently skipped. If none match, the caller logs a
            # warning naming every key that was tried.
            logger.debug(f"{key}: consType={cons_type} accepted but carries no values")
            if empty is None:
                empty = processed

        return empty if empty is not None else self._process_consumption_data(
            {}, self.CONS_TYPES[key][0], timeline_start, timeline_end
        )

    @staticmethod
    def _trim_timeline(processed, history_months):
        """
        Keep only the most recent `history_months` entries of the monthly chart.

        The request is anchored far in the past so that the total is measured
        from a fixed point, which makes the chart span the whole tenancy. The
        total has already been derived by the time this runs, so trimming only
        narrows what is published as the monthly breakdown and what the DIN
        comparison is calculated over - both of which are about recent
        behaviour rather than everything on record.
        """
        if not history_months or history_months <= 0:
            return
        processed["timeline"] = processed["timeline"][-history_months:]
        processed["reference_timeline"] = processed["reference_timeline"][-history_months:]

    @staticmethod
    def _cons_type_understood(raw_data, cons_type):
        """
        True if the portal actually understood `cons_type`.

        An unknown consType is not rejected - the portal silently answers with
        every consumption type at once, which would fold other categories'
        meters into this one's per-room table and total.
        """
        if not isinstance(raw_data, dict):
            return False
        key_figures = {
            entry.get("keyFigure")
            for entry in (raw_data.get("chart") or [])
            if isinstance(entry, dict) and entry.get("keyFigure") not in (None, "REF")
        }
        return key_figures <= {cons_type}

    def _process_consumption_data(self, raw_data, consumption_type, timeline_start, timeline_end):
        """
        Process raw consumption data into a structured format.

        Note: The Minol API currently only provides timeline data on aggregate level,
        not per individual room/device. Room timeline data is not available from the API.

        Args:
            raw_data (dict): Raw API response
            consumption_type (str): Type identifier (HEIZUNG, WARMWASSER, KALTWASSER)
            timeline_start (str): Start period (for documentation)
            timeline_end (str): End period (for documentation)

        Returns:
            dict: Processed data with by_room, overall timeline, and total_consumption
        """
        processed = {
            "by_room": [],
            "timeline": [],
            "reference_timeline": [],
            "total_consumption": 0.0,
            "total_source": "none",
            "cons_type": consumption_type,
        }

        if not isinstance(raw_data, dict):
            logger.warning(f"{consumption_type}: unexpected response type "
                           f"{type(raw_data).__name__}")
            return processed

        def num(value, default=0.0):
            try:
                if value in (None, ""):
                    return default
                return float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                return default

        def first(mapping, *keys, default=None):
            for k in keys:
                v = mapping.get(k)
                if v not in (None, ""):
                    return v
            return default

        table = raw_data.get("table") or []
        for room_data in table:
            if not isinstance(room_data, dict):
                continue

            internal_key = room_data.get("internalKey")
            device_number = first(
                room_data, "gerNr", "geraeteNr", "geraetenummer", "zaehlerNr", "zaehlernummer"
            )
            if device_number is None and internal_key and ";" in str(internal_key):
                device_number = str(internal_key).split(";")[-1].strip()

            room_name = first(
                room_data, "raum", "raumBez", "raumText", "bezeichnung",
                "beschreibung", "description",
            )
            if room_name is None or str(room_name).strip() == str(device_number or "").strip():
                room_name = first(room_data, "raum", "raumBez", "bezeichnung", default="Unknown")

            # Distinguish "the meter consumed 0" from "the row carried no
            # consumption field at all". Both used to read as 0.0, and a
            # spurious 0 looks like a meter reset to Home Assistant's
            # total_increasing handling, which then books the recovery as fresh
            # consumption and permanently double-counts the billing period.
            reported = first(room_data, "consumption", "verbrauch")
            consumption = num(reported, 0.0)

            room_info = {
                "room_name": str(room_name),
                "room_key": first(room_data, "raumKey", "raumId"),
                "device_number": device_number,
                "internal_key": internal_key,
                "consumption": consumption,
                "consumption_reported": reported is not None,
                "unit": first(room_data, "unit", "einheit", "einheitText", default="KWH"),
                "consumption_evaluated": num(first(room_data, "consumptionBew", "verbrauchBew", default=0)),
                "evaluation_score": first(room_data, "bewertung", "bewertungsfaktor"),
                "reading": num(first(room_data, "ablesung", "ablesungMax", "zaehlerstand", default=0)),
                "initial_reading": num(first(room_data, "anfangsstand", "ablesungMin", default=0)),
                # Note: Per-room timeline not available from API
            }
            processed["by_room"].append(room_info)
            processed["total_consumption"] += consumption

        if processed["by_room"]:
            processed["total_source"] = "table"

        for entry in raw_data.get("chart") or []:
            if not isinstance(entry, dict):
                continue
            timeline_entry = {
                "period": entry.get("category"),
                "period_int": entry.get("categoryInt"),
                "value": num(entry.get("value", 0)),
                "label": entry.get("label"),
                "num_values": entry.get("anzValues", 0),
            }
            is_ref = entry.get("keyFigure") == "REF" or entry.get("label") == "REF"
            if is_ref:
                processed["reference_timeline"].append(timeline_entry)
            else:
                processed["timeline"].append(timeline_entry)

        # Fall back to the monthly chart when the portal gives no per-room table
        # (observed for HEIZUNG with heat cost allocators).
        if not processed["by_room"] and processed["timeline"]:
            chart_total = sum(e["value"] for e in processed["timeline"])
            if chart_total:
                processed["total_consumption"] = chart_total
                processed["total_source"] = "chart"

        return processed

    def authenticate(self) -> bool:
        """
        Authenticate with the Minol portal.

        This is a convenience wrapper around login() + get_user_tenants().

        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            logger.info("Authenticating with Minol portal...")
            self.login()
            self.get_user_tenants()
            logger.info("Authentication successful")
            return True
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            self._authenticated = False
            return False

    def get_consumption_data(
        self,
        months_back: int = 12,
        force_update: bool = False
    ) -> Optional[Dict]:
        """
        Fetch all consumption data (heating, hot water, cold water) with caching.

        The totals always cover everything the portal holds, measured from a
        fixed anchor so they only ever grow; `months_back` narrows the monthly
        breakdown that goes out as sensor attributes, not the totals.

        Args:
            months_back: Months of monthly history to keep (default: 12)
            force_update: Force data refresh even if cached data exists

        Returns:
            Dict with consumption data or None on error
        """
        if not force_update and self._last_data and self._last_update:
            age = datetime.now() - self._last_update
            if age < self._cache_duration:
                logger.debug(f"Returning cached data (age: {age})")
                return self._last_data

        if not self._authenticated:
            if not self.authenticate():
                return None

        try:
            timeline_start = self.TIMELINE_ANCHOR
            timeline_end = datetime.now().strftime("%Y%m")

            logger.info(f"Fetching consumption data from {timeline_start} to "
                        f"{timeline_end} (portal clamps to the earliest reading "
                        f"it holds), keeping {months_back} months of history")

            data = self.get_all_consumption_data(
                timeline_start, timeline_end, history_months=months_back
            )
            self._last_data = data
            self._last_update = datetime.now()

            return data

        except Exception as e:
            logger.error(f"Error fetching consumption data: {e}")
            return None

    def get_heating_total(self) -> Optional[float]:
        """
        Get total heating consumption in kWh.

        Returns:
            float: Total heating consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "heating" in data and "error" not in data["heating"]:
            return data["heating"]["total_consumption"]
        return None

    def get_hot_water_total(self) -> Optional[float]:
        """
        Get total hot water consumption in m³.

        Returns:
            float: Total hot water consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "hot_water" in data and "error" not in data["hot_water"]:
            return data["hot_water"]["total_consumption"]
        return None

    def get_cold_water_total(self) -> Optional[float]:
        """
        Get total cold water consumption in m³.

        Returns:
            float: Total cold water consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "cold_water" in data and "error" not in data["cold_water"]:
            return data["cold_water"]["total_consumption"]
        return None

    def get_rooms_data(self, consumption_type: str = "heating") -> Optional[List[Dict]]:
        """
        Get per-room consumption data for a specific type.

        Args:
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            List of room data dictionaries or None on error
        """
        data = self.get_consumption_data()
        if data and consumption_type in data and "error" not in data[consumption_type]:
            return data[consumption_type]["by_room"]
        return None

    def get_room_consumption(self, room_name: str, consumption_type: str = "heating") -> Optional[float]:
        """
        Get consumption for a specific room.

        Args:
            room_name: Name of the room
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            Consumption value or None if not found
        """
        rooms = self.get_rooms_data(consumption_type)
        if rooms:
            for room in rooms:
                if room["room_name"] == room_name:
                    return room["consumption"]
        return None

    def get_timeline(self, consumption_type: str = "heating") -> Optional[List[Dict]]:
        """
        Get overall timeline data for a consumption type.

        Args:
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            List of timeline entries or None on error
        """
        data = self.get_consumption_data()
        if data and consumption_type in data and "error" not in data[consumption_type]:
            return data[consumption_type]["timeline"]
        return None

    def get_room_timeline(
        self,
        room_name: str,
        consumption_type: str = "heating"
    ) -> Optional[List[Dict]]:
        """
        Get timeline data for a specific room.

        Note: The Minol API currently does not provide per-room timeline data.
        This method returns None. Use get_timeline() for overall consumption timeline.

        Args:
            room_name: Name of the room
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            None (per-room timeline not available from API)
        """
        logger.warning("Per-room timeline data is not available from the Minol API")
        return None

    def get_user_details(self) -> Optional[Dict]:
        """
        Fetch detailed user information from the portal.

        Returns:
            dict: User details including customer number, property, address, etc.
                 Returns None on error.
        """
        if not self._authenticated:
            logger.error("Not authenticated. Call authenticate() first.")
            return None

        logger.info("Fetching user details...")
        url = f"{self.base_url}/minol.com~util~framework~ui5~common~web/rest/UserInfo/getUserDetail"

        headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/json; charset=utf-8',
            'Referer': f'{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true',
            'X-Requested-With': 'XMLHttpRequest'
        }

        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()

            user_details = response.json()
            logger.debug(f"User details response: {json.dumps(user_details, indent=2)}")
            return user_details
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching user details: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding user details response: {e}")
            return None

    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self._authenticated

    @property
    def user_info(self) -> Optional[Dict]:
        """Get user tenant information."""
        if self.user_tenants:
            return self.user_tenants[0] if self.user_tenants else None
        return None