"""
scalesync: Wyze Scale -> Garmin Connect

Fetches all available body composition records from a Wyze Scale and
uploads any not-yet-synced measurements to Garmin Connect.

Required environment variables:
  WYZE_EMAIL, WYZE_PASSWORD, WYZE_KEY_ID, WYZE_API_KEY
  GARMIN_EMAIL, GARMIN_PASSWORD

Optional:
  SYNC_INTERVAL  - minutes between sync runs (default: 30)
  DATA_DIR       - directory for persistent state (default: /data)
  DRY_RUN        - when "true", authenticate both services but skip uploads.
                   Default is "false" (live uploads enabled).
  DATE_FROM      - optional start date (YYYY-MM-DD) for backfill runs.
  DATE_TO        - optional end date (YYYY-MM-DD) for backfill runs.
                   If no date range is provided, only today's records are synced.
"""

import hashlib
import logging
import os
import re
import time as _time
from datetime import date, datetime, timedelta, timezone

import requests
from wyze_sdk import Client
from wyze_sdk.errors import WyzeApiError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WYZE_EMAIL = os.environ["WYZE_EMAIL"]
WYZE_PASSWORD = os.environ["WYZE_PASSWORD"]
WYZE_KEY_ID = os.environ["WYZE_KEY_ID"]
WYZE_API_KEY = os.environ["WYZE_API_KEY"]

GARMIN_COOKIES_ENV = os.environ.get("GARMIN_COOKIES", "").strip()

_sync_interval_raw = os.environ.get("SYNC_INTERVAL", "30")
try:
    SYNC_INTERVAL = int(_sync_interval_raw)
except ValueError:
    raise ValueError(f"SYNC_INTERVAL must be an integer, got: {_sync_interval_raw!r}")
if not (1 <= SYNC_INTERVAL <= 1440):
    raise ValueError(f"SYNC_INTERVAL must be between 1 and 1440 minutes, got: {SYNC_INTERVAL}")

_data_dir_raw = os.environ.get("DATA_DIR", "/data")
DATA_DIR = os.path.realpath(os.path.abspath(_data_dir_raw))
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
DATE_FROM = os.environ.get("DATE_FROM", "").strip()
DATE_TO = os.environ.get("DATE_TO", "").strip()

GARMIN_COOKIE_FILE = os.path.join(DATA_DIR, "garmin_cookies")
GARMIN_BACKOFF_FILE = os.path.join(DATA_DIR, "garmin_auth_backoff")
SYNCED_FILE = os.path.join(DATA_DIR, "synced.txt")

# Wyze weight is reported in lbs; Garmin requires kg
LBS_TO_KG = 0.45359237

# Newer/unknown Wyze scale variants may come through as product_type="Common"
# until wyze-sdk adds explicit mappings.
KNOWN_WYZE_SCALE_MODELS = {
    "WL_SC2",
    "WL_SCA",
    "WL_SCL",
    "WL_SCU",
}


def resolve_date_range() -> tuple[date, date]:
    """Resolve desired sync date range from env, defaulting to today."""
    if not DATE_FROM and not DATE_TO:
        today = datetime.now().date()
        return today, today

    try:
        if DATE_FROM:
            start = datetime.strptime(DATE_FROM, "%Y-%m-%d").date()
        elif DATE_TO:
            start = datetime.strptime(DATE_TO, "%Y-%m-%d").date()
        else:
            raise RuntimeError("Unreachable date range parsing branch.")

        if DATE_TO:
            end = datetime.strptime(DATE_TO, "%Y-%m-%d").date()
        else:
            end = start
    except ValueError as exc:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {exc}") from exc

    if start > end:
        raise ValueError(f"DATE_FROM ({start}) cannot be after DATE_TO ({end}).")

    return start, end


# ---------------------------------------------------------------------------
# Garmin client (cookie-based)
# ---------------------------------------------------------------------------
# NOTE: Garmin's OAuth SSO has been globally blocked for automated clients
# since March 2026.  We use browser session cookies instead, which support
# the weight-service endpoint.  Full body-composition FIT uploads require
# OAuth and are not available via this path.

class GarminRateLimitBackoff(Exception):
    """Raised when garmin_auth skips the attempt because a prior 429 is still
    within its self-imposed backoff window."""


# How long to self-impose a backoff after a 429 (26 h keeps us clear of the
# daily 24-h ban window while still retrying on the second day after it).
_GARMIN_BACKOFF_SECONDS = 26 * 3600


def _is_garmin_rate_limit(exc: Exception) -> bool:
    """Return True if exc represents a Garmin 429 / rate-limit response."""
    return "429" in str(exc)


def _write_garmin_backoff() -> None:
    """Write the backoff timestamp file so the next run skips auth."""
    retry_after = _time.time() + _GARMIN_BACKOFF_SECONDS
    try:
        with open(GARMIN_BACKOFF_FILE, "w") as f:
            f.write(str(retry_after))
    except OSError:
        pass


class GarminCookieClient:
    """Thin Garmin Connect client backed by browser session cookies.

    Supports weight-only uploads via the app/proxy/weight-service endpoint.
    Body-composition FIT uploads (which require OAuth) are not available.
    """

    _HOME_URL = "https://connect.garmin.com/"
    # New Connect app uses /app/proxy/ (old /modern/proxy/ redirects to SSO)
    _WEIGHT_URL = "https://connect.garmin.com/app/proxy/weight-service/user-weight"

    def __init__(self, cookie_str: str) -> None:
        self._cookie_str = cookie_str
        self._session = requests.Session()
        # Send cookies as a raw header — preserves all cookie attributes and
        # avoids domain-matching issues with the requests cookie jar.
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
            "Origin": "https://connect.garmin.com",
            "Referer": "https://connect.garmin.com/",
            "Cookie": cookie_str,
            "NK": "NT",
        })
        self._csrf_token: str | None = None

    def _try_refresh_jwt(self) -> bool:
        """Follow Garmin's SSO redirect to obtain a fresh JWT_WEB.

        When JWT_WEB expires (~2 h), Garmin redirects API calls to SSO.
        If the session cookie is still valid (~3 weeks), SSO will issue a
        new JWT_WEB and redirect back.  We capture the new token from the
        session cookie jar and update our Cookie header.
        """
        try:
            sso_url = (
                "https://sso.garmin.com/portal/sso/sign-in"
                "?clientId=GarminConnect"
                "&service=https%3A%2F%2Fconnect.garmin.com%2F"
            )
            # Follow redirects so the final connect.garmin.com response can
            # set a new JWT_WEB via Set-Cookie.
            self._session.get(sso_url, allow_redirects=True, timeout=20)
            jwt = self._session.cookies.get("JWT_WEB")
            if jwt:
                # Splice the new JWT_WEB into our Cookie header string.
                parts = [
                    p.strip() for p in self._cookie_str.split(";")
                    if not p.strip().startswith("JWT_WEB=")
                ]
                parts.insert(0, f"JWT_WEB={jwt}")
                self._cookie_str = "; ".join(parts)
                self._session.headers["Cookie"] = self._cookie_str
                log.info("Garmin JWT_WEB refreshed via SSO session.")
                return True
        except Exception as exc:
            log.debug("JWT refresh attempt failed: %s", exc)
        return False

    def _ensure_csrf(self) -> None:
        if self._csrf_token:
            return
        try:
            resp = self._session.get(self._HOME_URL, timeout=15)
            m = re.search(
                r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\'](.*?)["\']',
                resp.text,
            )
            if m:
                self._csrf_token = m.group(1)
        except Exception:
            pass  # proceed without CSRF token; endpoint may not require it

    def _post_weight(self, weight_kg: float, local_ts: str, gmt_ts: str) -> requests.Response:
        headers = {"Content-Type": "application/json"}
        if self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token
        return self._session.post(
            self._WEIGHT_URL,
            json={
                "dateTimestamp": local_ts,
                "gmtTimestamp": gmt_ts,
                "unitKey": "kg",
                "sourceType": "MANUAL",
                "value": weight_kg,
            },
            headers=headers,
            allow_redirects=False,
            timeout=30,
        )

    def upload_weight(self, weight_kg: float, timestamp: str) -> None:
        """POST a weight-only measurement to Garmin Connect.

        weight_kg: weight in kilograms
        timestamp: ISO-8601 string (UTC preferred)
        """
        self._ensure_csrf()
        dt = datetime.fromisoformat(timestamp)
        local_ts = dt.strftime("%Y-%m-%dT%H:%M:%S.000")
        gmt_ts = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000")

        resp = self._post_weight(weight_kg, local_ts, gmt_ts)

        if resp.status_code in (301, 302, 303, 307, 308):
            # JWT_WEB likely expired — try refreshing via the SSO session flow.
            log.info("Garmin returned redirect; attempting JWT refresh.")
            if self._try_refresh_jwt():
                self._csrf_token = None  # may have changed after SSO round-trip
                self._ensure_csrf()
                resp = self._post_weight(weight_kg, local_ts, gmt_ts)

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            raise RuntimeError(
                f"Garmin redirected weight upload to {location!r} — "
                f"session cookie has expired. Re-run generate_cookies.py "
                f"and update the GARMIN_COOKIES secret."
            )
        if resp.status_code == 429:
            raise RuntimeError(f"429 Too Many Requests: {resp.text[:200]}")
        resp.raise_for_status()


def garmin_auth() -> GarminCookieClient:
    """Load Garmin session cookies and return a ready client.

    Checks a self-imposed backoff file first.  If a prior upload 429 is still
    within its window, raises GarminRateLimitBackoff so the caller exits cleanly.
    """
    # --- check self-imposed backoff ---
    if os.path.exists(GARMIN_BACKOFF_FILE):
        try:
            retry_after = float(open(GARMIN_BACKOFF_FILE).read().strip())
            remaining = retry_after - _time.time()
            if remaining > 0:
                hrs = remaining / 3600
                raise GarminRateLimitBackoff(
                    f"Garmin upload skipped: still in rate-limit backoff "
                    f"({hrs:.1f}h remaining). Sync will resume automatically."
                )
        except GarminRateLimitBackoff:
            raise
        except Exception:
            pass  # corrupt file — proceed anyway

    # --- load cookie string ---
    cookie_str = GARMIN_COOKIES_ENV
    if not cookie_str and os.path.exists(GARMIN_COOKIE_FILE):
        try:
            with open(GARMIN_COOKIE_FILE) as f:
                cookie_str = f.read().strip()
        except OSError:
            pass

    if not cookie_str:
        raise RuntimeError(
            "No Garmin session cookies found. "
            "Run generate_cookies.py locally and store the output as "
            "the GARMIN_COOKIES GitHub secret."
        )

    # Persist to file so the cache can carry it across runs.
    if GARMIN_COOKIES_ENV and not os.path.exists(GARMIN_COOKIE_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(GARMIN_COOKIE_FILE, "w") as f:
                f.write(cookie_str)
        except OSError:
            pass

    log.info("Garmin cookie client initialized.")
    return GarminCookieClient(cookie_str)


# ---------------------------------------------------------------------------
# Wyze authentication
# ---------------------------------------------------------------------------

def wyze_auth() -> str:
    """Authenticate with Wyze and return an access token."""
    try:
        response = Client().login(
            email=WYZE_EMAIL,
            password=WYZE_PASSWORD,
            key_id=WYZE_KEY_ID,
            api_key=WYZE_API_KEY,
        )
        token = response.get("access_token")
        if not token:
            raise RuntimeError("Wyze login succeeded but no access_token returned.")
        log.info("Wyze authentication successful.")
        return token
    except WyzeApiError as exc:
        raise RuntimeError(f"Wyze authentication failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Checksum helpers
# ---------------------------------------------------------------------------

def load_synced() -> set:
    """Load the set of already-synced record checksums."""
    try:
        with open(SYNCED_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def mark_synced(checksum: str) -> None:
    """Append a checksum to the synced file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SYNCED_FILE, "a", encoding="utf-8") as f:
        f.write(checksum + "\n")


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def _float(val):
    return float(val) if val is not None else None


def _int(val):
    return int(val) if val is not None else None


def _record_payload(record) -> dict:
    """Map a Wyze record to python-garminconnect add_body_composition fields."""
    weight_kg = _float(record.weight) * LBS_TO_KG if record.weight is not None else None
    bmr = _float(getattr(record, "bmr", None))
    body_vfr = _float(record.body_vfr)
    timestamp = datetime.fromtimestamp(int(record.measure_ts) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")

    return {
        "timestamp": timestamp,
        "weight": weight_kg,
        "percent_fat": _float(record.body_fat),
        "percent_hydration": _float(record.body_water),
        "visceral_fat_mass": body_vfr,
        "bone_mass": _float(record.bone_mineral),
        "muscle_mass": _float(record.muscle),
        "basal_met": _int(bmr) if bmr is not None else None,
        "active_met": int(bmr * 1.25) if bmr is not None else None,
        "physique_rating": _int(body_type) if (body_type := getattr(record, "body_type", None)) is not None else 5,
        "metabolic_age": _int(getattr(record, "metabolic_age", None)),
        "visceral_fat_rating": _int(body_vfr),
        "bmi": _float(record.bmi),
    }


def checksum_payload(payload: dict) -> str:
    canonical = "|".join(str(payload[k]) for k in sorted(payload.keys()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def log_wyze_record(record, checksum: str) -> None:
    """Log details of a Wyze record that would be uploaded."""
    weight_lbs = _float(record.weight)
    weight_kg = weight_lbs * LBS_TO_KG if weight_lbs is not None else None
    log.info(
        "[DRY-RUN] Wyze record details: "
        "ts=%s  weight=%.1f lbs (%.3f kg)  body_fat=%s%%  body_water=%s%%  "
        "bmi=%s  muscle=%s  bone_mineral=%s  body_vfr=%s  bmr=%s  "
        "metabolic_age=%s  body_type=%s",
        record.measure_ts,
        weight_lbs or 0,
        weight_kg or 0,
        getattr(record, "body_fat", None),
        getattr(record, "body_water", None),
        getattr(record, "bmi", None),
        getattr(record, "muscle", None),
        getattr(record, "bone_mineral", None),
        getattr(record, "body_vfr", None),
        getattr(record, "bmr", None),
        getattr(record, "metabolic_age", None),
        getattr(record, "body_type", None),
    )
    log.info("[DRY-RUN] Upload payload checksum: sha256=%s", checksum)


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_once(
    wyze_token: str | None = None,
    garmin_client: GarminCookieClient | None = None,
) -> int:
    """Run one sync cycle: fetch Wyze records, upload new ones to Garmin.

    Pre-authenticated clients can be passed in to avoid re-authenticating on
    each retry. If omitted, both services are authenticated fresh.
    """
    log.info("--- Starting sync (DRY_RUN=%s) ---", DRY_RUN)
    uploaded = 0
    skipped = 0
    dry_run_logged = 0
    window_start, window_end = resolve_date_range()
    log.info("Sync date window: %s to %s", window_start, window_end)

    # Authenticate both services regardless of dry-run mode
    access_token = wyze_token if wyze_token is not None else wyze_auth()
    garmin_client = garmin_client if garmin_client is not None else garmin_auth()

    synced = load_synced()

    # Connect to Wyze and find scale devices
    client = Client(token=access_token)
    devices = client.devices_list()
    log.info("Wyze devices found: %d total", len(devices))

    def _is_scale_device(device) -> bool:
        device_type = getattr(device, "type", "")
        if device_type == "WyzeScale":
            return True

        product_model = (getattr(device, "product_model", "") or "").upper()
        product_type = (getattr(device, "product_type", "") or "").lower()
        mac = (getattr(device, "mac", "") or "").upper()
        nickname = (getattr(device, "nickname", "") or "").lower()

        if product_model in KNOWN_WYZE_SCALE_MODELS:
            return True
        if product_model.startswith("WL_SC"):
            return True
        if product_type == "scale":
            return True
        if mac.startswith("WL_SC"):
            return True
        if "scale" in nickname:
            return True

        return False

    scale_devices = [d for d in devices if _is_scale_device(d)]

    if not scale_devices:
        # Helpful diagnostics when wyze-sdk labels devices as Unknown.
        for device in devices:
            log.warning(
                "Device rejected by scale filter: type=%s product_model=%s "
                "product_type=%s mac=%s nickname=%s",
                getattr(device, "type", None),
                getattr(device, "product_model", None),
                getattr(device, "product_type", None),
                getattr(device, "mac", None),
                getattr(device, "nickname", None),
            )
        log.warning("No Wyze Scale devices found on this account.")
        return 0

    for device in scale_devices:
        log.info("Processing scale: %s (%s)", device.nickname or device.mac, device.mac)
        records = []

        try:
            scale = client.scales.info(device_mac=device.mac)
        except WyzeApiError as exc:
            log.error("Failed to fetch scale info for %s: %s", device.mac, exc)
            scale = None

        if scale is None:
            log.warning("Wyze returned no scale info for %s; trying get_records fallback.", device.mac)
        else:
            records = scale.latest_records or []

        # Some models are not supported by scales.info() but still expose
        # measurement history via get_records().
        if not records:
            get_records = getattr(client.scales, "get_records", None)
            if callable(get_records):
                start_time = datetime.combine(window_start, datetime.min.time())
                end_time = datetime.combine(window_end + timedelta(days=1), datetime.min.time())

                model_candidates = []
                product_model = (getattr(device, "product_model", "") or "").strip()
                if product_model:
                    model_candidates.append(product_model)
                model_candidates.extend(["JA.SC", "JA.SC2"])

                for model in model_candidates:
                    try:
                        fallback_records = get_records(
                            device_model=model,
                            start_time=start_time,
                            end_time=end_time,
                        )
                        if fallback_records:
                            records = list(fallback_records)
                            log.info(
                                "Found %d record(s) via get_records fallback (device_model=%s).",
                                len(records),
                                model,
                            )
                            break
                    except Exception as exc:
                        log.warning(
                            "get_records fallback failed for device_model=%s: %s",
                            model,
                            exc,
                        )
            else:
                log.warning("wyze-sdk has no scales.get_records() method; cannot use fallback.")

        log.info("Found %d record(s) for this scale.", len(records))

        filtered_records = []
        for record in records:
            record_date = datetime.fromtimestamp(int(record.measure_ts) / 1000, tz=timezone.utc).date()
            if window_start <= record_date <= window_end:
                filtered_records.append(record)
        records = filtered_records
        log.info("Found %d record(s) in selected date window.", len(records))

        for record in records:
            payload = _record_payload(record)
            checksum = checksum_payload(payload)

            if checksum in synced:
                skipped += 1
                continue

            if DRY_RUN:
                log_wyze_record(record, checksum)
                dry_run_logged += 1
                continue

            if payload["weight"] is None:
                log.warning("Skipping record ts=%s: no weight value.", record.measure_ts)
                continue

            try:
                garmin_client.upload_weight(payload["weight"], payload["timestamp"])
                mark_synced(checksum)
                synced.add(checksum)
                uploaded += 1
                log.info(
                    "Uploaded: ts=%s  weight=%.1f lbs",
                    record.measure_ts,
                    _float(record.weight) if record.weight else 0,
                )
            except Exception as exc:
                if _is_garmin_rate_limit(exc):
                    _write_garmin_backoff()
                    raise RuntimeError(
                        f"Garmin rate-limited during upload; "
                        f"backoff set for {_GARMIN_BACKOFF_SECONDS // 3600}h. "
                        f"Stopping retries: {exc}"
                    ) from exc
                if "redirected" in str(exc).lower():
                    # Auth failure — retrying will not help.
                    raise RuntimeError(str(exc)) from exc
                log.error("Failed to upload record ts=%s: %s", record.measure_ts, exc)

    if DRY_RUN:
        log.info(
            "Sync complete (DRY-RUN): %d would-be uploads logged, %d already synced.",
            dry_run_logged,
            skipped,
        )
        return dry_run_logged
    else:
        log.info("Sync complete: %d uploaded, %d already synced.", uploaded, skipped)
        return uploaded


def main() -> None:
    log.info("scalesync starting. Sync interval: %d minutes.", SYNC_INTERVAL)
    while True:
        try:
            sync_once()
        except Exception as exc:
            log.error("Sync cycle failed: %s", exc)
        log.info("Sleeping %d minutes until next sync...", SYNC_INTERVAL)
        _time.sleep(SYNC_INTERVAL * 60)


if __name__ == "__main__":
    main()
