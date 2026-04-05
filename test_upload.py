"""
Test whether Garmin's FIT upload endpoint accepts browser session cookies.

If it does, we can bypass the broken OAuth SSO entirely and keep full
body composition uploads.  If it doesn't, we fall back to weight-only
via the new gc-api endpoint.

Usage (run in a real terminal):
    python3 test_upload.py

You will be prompted to paste your browser cookies.  Get them from:
  1. Open Chrome → connect.garmin.com (already logged in)
  2. DevTools → Network tab → click any connectapi.garmin.com request
  3. Under Request Headers, copy the entire value of the "cookie:" header
"""
import io
import os
import struct
import sys
import time

# ── cookie input ─────────────────────────────────────────────────────────────

COOKIE_ENV = os.environ.get("GARMIN_COOKIES", "").strip()

if COOKIE_ENV:
    cookie_str = COOKIE_ENV
    print(f"Using cookies from GARMIN_COOKIES env var ({len(cookie_str)} chars).")
else:
    print("Paste the full value of the 'cookie:' request header from DevTools,")
    print("then press Enter twice:\n")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "" and lines:
            break
        lines.append(line)
    cookie_str = " ".join(lines).strip()

if not cookie_str:
    print("ERROR: no cookies provided.")
    sys.exit(1)

print(f"\nCookies received ({len(cookie_str)} chars).")

# ── minimal FIT file (weight-scale message) ───────────────────────────────────
# We replicate what garminconnect.FitEncoderWeight produces for a single
# dummy reading (70 kg, no body-comp fields) so we don't need real data.

def _fit_timestamp(dt_epoch: float) -> int:
    """Convert a Unix epoch to a FIT timestamp (seconds since 1989-12-31)."""
    FIT_EPOCH = 631065600  # 1989-12-31 00:00:00 UTC
    return int(dt_epoch) - FIT_EPOCH


def _pack_u16(val, scale) -> bytes:
    """Scale a float value and pack as uint16 (little-endian).  0xFFFF = invalid."""
    if val is None:
        return struct.pack("<H", 0xFFFF)
    return struct.pack("<H", int(round(val * scale)))


def build_fit_weight(weight_kg: float, ts: float | None = None) -> bytes:
    """Build the smallest valid FIT file for a single weight-scale record."""
    if ts is None:
        ts = time.time()
    fit_ts = _fit_timestamp(ts)

    buf = io.BytesIO()

    def _write(data: bytes) -> None:
        buf.write(data)

    # ---- File Header (14 bytes) ----
    header_data = struct.pack(
        "<BBHI4s",
        14,           # header length
        0x10,         # protocol version 1.0
        2132,         # profile version (matches garminconnect library)
        0,            # data size placeholder — filled below
        b".FIT",      # signature
    )
    _write(header_data)

    data_start = buf.tell()

    # ---- Definition message: file_id (global msg 0) ----
    # Record header: definition, local msg type 0
    _write(bytes([0x40]))
    _write(struct.pack("<BBH", 0, 0, 0))  # reserved, architecture, global msg num
    # fields: serial_number(3,uint32), time_created(4,uint32), manufacturer(1,uint16),
    #         product(2,uint16), type(0,enum)
    _write(struct.pack("B", 5))  # field count
    _write(struct.pack("BBB", 3, 4, 0x8C))  # serial_number: uint32
    _write(struct.pack("BBB", 4, 4, 0x8C))  # time_created: uint32
    _write(struct.pack("BBB", 1, 2, 0x84))  # manufacturer: uint16
    _write(struct.pack("BBB", 2, 2, 0x84))  # product: uint16
    _write(struct.pack("BBB", 0, 1, 0x00))  # type: enum

    # ---- Data message: file_id ----
    _write(bytes([0x00]))
    _write(struct.pack("<I", 0xFFFFFFFF))   # serial_number (invalid)
    _write(struct.pack("<I", fit_ts))        # time_created
    _write(struct.pack("<H", 255))           # manufacturer = Development
    _write(struct.pack("<H", 0xFFFF))        # product (invalid)
    _write(bytes([4]))                        # type = weight (4)

    # ---- Definition message: device_info (global msg 23) ----
    _write(bytes([0x41]))  # definition, local msg type 1
    _write(struct.pack("<BBH", 0, 0, 23))   # device_info
    _write(struct.pack("B", 3))              # 3 fields
    _write(struct.pack("BBB", 253, 4, 0x8C))  # timestamp: uint32
    _write(struct.pack("BBB", 0, 2, 0x84))    # device_index: uint16
    _write(struct.pack("BBB", 4, 1, 0x02))    # source_type: uint8

    # ---- Data message: device_info ----
    _write(bytes([0x01]))
    _write(struct.pack("<I", fit_ts))
    _write(struct.pack("<H", 0xFFFF))  # device_index invalid
    _write(bytes([5]))                  # source_type = antplus

    # ---- Definition message: weight_scale (global msg 30) ----
    LMSG_WEIGHT = 3
    _write(bytes([0x40 | LMSG_WEIGHT]))  # definition, local msg type 3
    _write(struct.pack("<BBH", 0, 0, 30))  # weight_scale
    _write(struct.pack("B", 2))             # 2 fields (timestamp + weight only)
    _write(struct.pack("BBB", 253, 4, 0x8C))  # timestamp: uint32
    _write(struct.pack("BBB", 0,   2, 0x84))  # weight: uint16 (scale 100)

    # ---- Data message: weight_scale ----
    _write(bytes([LMSG_WEIGHT]))
    _write(struct.pack("<I", fit_ts))
    _write(struct.pack("<H", int(round(weight_kg * 100))))

    data_end = buf.tell()

    # ---- Patch data size in file header ----
    buf.seek(4)
    buf.write(struct.pack("<I", data_end - data_start))

    # ---- CRC (2 bytes, we write 0x0000 — Garmin ignores bad CRC on upload) ----
    buf.seek(0, 2)
    buf.write(struct.pack("<H", 0))

    return buf.getvalue()


fit_data = build_fit_weight(70.0)
print(f"FIT file built: {len(fit_data)} bytes")

# ── HTTP helpers ──────────────────────────────────────────────────────────────

try:
    import requests
except ImportError:
    print("\nERROR: 'requests' is not installed.  Run:  pip install requests")
    sys.exit(1)

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Origin": "https://connect.garmin.com",
    "Referer": "https://connect.garmin.com/",
})

# Parse cookie string into the session jar
for part in cookie_str.split(";"):
    part = part.strip()
    if "=" in part:
        name, _, val = part.partition("=")
        session.cookies.set(name.strip(), val.strip(), domain=".garmin.com")

# ── Attempt 1: connectapi.garmin.com (OAuth endpoint, FIT upload) ─────────────

print("\n--- Attempt 1: connectapi.garmin.com/upload-service/upload ---")
url1 = "https://connectapi.garmin.com/upload-service/upload"
try:
    resp1 = session.post(
        url1,
        files={"file": ("body_composition.fit", fit_data, "application/octet-stream")},
        headers={"NK": "NT"},  # garth always sends this
        timeout=20,
    )
    print(f"  Status: {resp1.status_code}")
    print(f"  Body:   {resp1.text[:500]}")
except Exception as exc:
    print(f"  ERROR:  {exc}")

# ── Attempt 2: connect.garmin.com/gc-api (new web API) ───────────────────────

print("\n--- Attempt 2: connect.garmin.com/gc-api/upload-service/upload ---")
url2 = "https://connect.garmin.com/gc-api/upload-service/upload"

# Grab a CSRF token from the main page first
csrf = None
try:
    page = session.get("https://connect.garmin.com/", timeout=15)
    if "csrf-token" in page.text:
        import re
        m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\'](.*?)["\']', page.text)
        if m:
            csrf = m.group(1)
            print(f"  CSRF token obtained: {csrf[:20]}...")
except Exception as exc:
    print(f"  Could not fetch CSRF token: {exc}")

hdrs2 = {}
if csrf:
    hdrs2["X-CSRF-Token"] = csrf

try:
    resp2 = session.post(
        url2,
        files={"file": ("body_composition.fit", fit_data, "application/octet-stream")},
        headers=hdrs2,
        timeout=20,
    )
    print(f"  Status: {resp2.status_code}")
    print(f"  Body:   {resp2.text[:500]}")
except Exception as exc:
    print(f"  ERROR:  {exc}")

# ── Attempt 3: connect.garmin.com/modern/proxy upload ────────────────────────

print("\n--- Attempt 3: connect.garmin.com/modern/proxy/upload-service/upload ---")
url3 = "https://connect.garmin.com/modern/proxy/upload-service/upload"
try:
    resp3 = session.post(
        url3,
        files={"file": ("body_composition.fit", fit_data, "application/octet-stream")},
        headers=hdrs2,
        timeout=20,
    )
    print(f"  Status: {resp3.status_code}")
    print(f"  Body:   {resp3.text[:500]}")
except Exception as exc:
    print(f"  ERROR:  {exc}")

print("\nDone. Review the status codes above:")
print("  2xx = cookies work for upload (full body composition is viable)")
print("  401/403 = OAuth required (fall back to weight-only via gc-api)")
print("  429 = rate limited (try again later)")
