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
  DRY_RUN        - when "true" (default), authenticate both services but skip
                   the actual Garmin upload; log all Wyze data and the FIT file
                   that would have been sent. Set to "false" to enable uploads.
"""

import hashlib
import io
import logging
import os
import time
from datetime import datetime, timedelta

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
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"

GARMIN_TOKENS_DIR = os.path.join(DATA_DIR, "garmin_tokens")
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


def log_wyze_record(record, fit_bytes: bytes):
    """Log full details of a Wyze record and the FIT file that would be uploaded."""
    checksum = hashlib.md5(fit_bytes).hexdigest()
    weight_lbs = float(record.weight) if record.weight is not None else None
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
    log.info(
        "[DRY-RUN] FIT file that would be uploaded: md5=%s  size=%d bytes",
        checksum,
        len(fit_bytes),
    )


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_once():
    """Run one sync cycle: fetch Wyze records, upload new ones to Garmin."""
    log.info("--- Starting sync (DRY_RUN=%s) ---", DRY_RUN)
    uploaded = 0
    skipped = 0
    dry_run_logged = 0

    # Authenticate both services regardless of dry-run mode
    access_token = wyze_auth()
    garmin_auth()

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
        log.warning("No Wyze Scale devices found on this account.")
        return

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
                end_time = datetime.utcnow()
                start_time = end_time - timedelta(days=3650)

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

            if DRY_RUN:
                log_wyze_record(record, fit_bytes)
                dry_run_logged += 1
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

    if DRY_RUN:
        log.info(
            "Sync complete (DRY-RUN): %d would-be uploads logged, %d already synced.",
            dry_run_logged,
            skipped,
        )
    else:
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
