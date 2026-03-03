"""
scalesync: Wyze Scale → Garmin Connect

Fetches all available body composition records from a Wyze Scale and
uploads any not-yet-synced measurements to Garmin Connect as FIT files.

Required environment variables:
  WYZE_EMAIL, WYZE_PASSWORD, WYZE_KEY_ID, WYZE_API_KEY
  GARMIN_EMAIL, GARMIN_PASSWORD

Optional:
  SYNC_INTERVAL  - minutes between sync runs (default: 30)
  DATA_DIR       - directory for persistent state (default: /data)
"""

import hashlib
import io
import logging
import os
import time

import garth
from wyze_sdk import Client
from wyze_sdk.errors import WyzeApiError

from fit import FitEncoder_Weight

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

GARMIN_EMAIL = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "30"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")

GARMIN_TOKENS_DIR = os.path.join(DATA_DIR, "garmin_tokens")
SYNCED_FILE = os.path.join(DATA_DIR, "synced.txt")

# Wyze weight is reported in lbs; Garmin requires kg
LBS_TO_KG = 0.45359237


# ---------------------------------------------------------------------------
# Garmin authentication
# ---------------------------------------------------------------------------

def garmin_auth():
    """Authenticate with Garmin Connect, persisting OAuth tokens to disk."""
    os.makedirs(GARMIN_TOKENS_DIR, exist_ok=True)
    try:
        garth.resume(GARMIN_TOKENS_DIR)
        garth.client.username  # verify token is usable
        log.info("Resumed Garmin session from saved tokens.")
    except Exception:
        log.info("No valid saved tokens — logging in to Garmin Connect.")
        garth.login(GARMIN_EMAIL, GARMIN_PASSWORD)
        garth.save(GARMIN_TOKENS_DIR)
        log.info("Garmin tokens saved to %s", GARMIN_TOKENS_DIR)


# ---------------------------------------------------------------------------
# Wyze authentication
# ---------------------------------------------------------------------------

def wyze_auth():
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
    """Load the set of already-synced FIT file checksums."""
    if not os.path.exists(SYNCED_FILE):
        return set()
    with open(SYNCED_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def mark_synced(checksum: str):
    """Append a checksum to the synced file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SYNCED_FILE, "a") as f:
        f.write(checksum + "\n")


# ---------------------------------------------------------------------------
# FIT file building
# ---------------------------------------------------------------------------

def build_fit(record) -> bytes:
    """Convert a Wyze ScaleRecord into a FIT file (bytes)."""
    timestamp_s = int(record.measure_ts) // 1000

    weight_kg = float(record.weight) * LBS_TO_KG if record.weight is not None else None

    def _float(val):
        return float(val) if val is not None else None

    def _int(val):
        return int(val) if val is not None else None

    body_fat = _float(record.body_fat)
    body_water = _float(record.body_water)
    body_vfr = _float(record.body_vfr)
    bone_mineral = _float(record.bone_mineral)
    muscle = _float(record.muscle)
    bmr = _float(getattr(record, "bmr", None))
    metabolic_age = _int(getattr(record, "metabolic_age", None))
    body_type = _int(getattr(record, "body_type", None)) or 5
    bmi = _float(record.bmi)

    basal_met = _int(bmr) if bmr is not None else None
    active_met = int(bmr * 1.25) if bmr is not None else None

    # Approximate visceral fat mass from visceral fat rating (1 rating ≈ 1 unit)
    visceral_fat_mass = body_vfr

    enc = FitEncoder_Weight()
    enc.write_file_info(time_created=timestamp_s)
    enc.write_file_creator()
    enc.write_device_info(timestamp=timestamp_s)
    enc.write_weight_scale(
        timestamp=timestamp_s,
        weight=weight_kg,
        percent_fat=body_fat,
        percent_hydration=body_water,
        visceral_fat_mass=visceral_fat_mass,
        bone_mass=bone_mineral,
        muscle_mass=muscle,
        basal_met=basal_met,
        physique_rating=body_type,
        active_met=active_met,
        metabolic_age=metabolic_age,
        visceral_fat_rating=_int(body_vfr),
        bmi=bmi,
    )
    return enc.finish()


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_once():
    """Run one sync cycle: fetch Wyze records, upload new ones to Garmin."""
    log.info("--- Starting sync ---")
    uploaded = 0
    skipped = 0

    # Authenticate
    access_token = wyze_auth()
    garmin_auth()

    synced = load_synced()

    # Connect to Wyze and find scale devices
    client = Client(token=access_token)
    devices = client.devices_list()
    log.info("Wyze devices found: %d total", len(devices))
    scale_devices = [
        d for d in devices
        if d.type == "WyzeScale" or getattr(d, "product_model", "") == "WL_SCU"
    ]

    if not scale_devices:
        log.warning("No Wyze Scale devices found on this account.")
        return

    for device in scale_devices:
        log.info("Processing scale: %s (%s)", device.nickname or device.mac, device.mac)
        try:
            scale = client.scales.info(device_mac=device.mac)
        except WyzeApiError as exc:
            log.error("Failed to fetch scale info for %s: %s", device.mac, exc)
            continue

        records = scale.latest_records or []
        log.info("Found %d record(s) for this scale.", len(records))

        for record in records:
            try:
                fit_bytes = build_fit(record)
            except Exception as exc:
                log.error("Failed to build FIT for record ts=%s: %s", record.measure_ts, exc)
                continue

            checksum = hashlib.md5(fit_bytes).hexdigest()

            if checksum in synced:
                skipped += 1
                continue

            try:
                garth.client.upload(io.BytesIO(fit_bytes))
                mark_synced(checksum)
                synced.add(checksum)
                uploaded += 1
                log.info(
                    "Uploaded: ts=%s  weight=%.1f lbs",
                    record.measure_ts,
                    float(record.weight) if record.weight else 0,
                )
            except Exception as exc:
                log.error("Failed to upload record ts=%s: %s", record.measure_ts, exc)

    log.info("Sync complete: %d uploaded, %d already synced.", uploaded, skipped)


def main():
    log.info("scalesync starting. Sync interval: %d minutes.", SYNC_INTERVAL)
    while True:
        try:
            sync_once()
        except Exception as exc:
            log.error("Sync cycle failed: %s", exc)
        log.info("Sleeping %d minutes until next sync...", SYNC_INTERVAL)
        time.sleep(SYNC_INTERVAL * 60)


if __name__ == "__main__":
    main()
