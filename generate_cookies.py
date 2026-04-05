"""
Capture Garmin Connect browser session cookies for use as a GitHub secret.

Garmin's OAuth SSO has blocked automated clients since March 2026.  This
script reads your browser cookies from the clipboard and packages them for
storage as the GARMIN_COOKIES GitHub Actions secret.

Steps:
  1. Open Chrome and log in to connect.garmin.com
  2. Open DevTools (F12) → Network tab
  3. Click any request to connect.garmin.com or connectapi.garmin.com
  4. Under "Request Headers", right-click the "cookie:" header value
     and choose "Copy value"
  5. Run this script — it reads the cookie directly from your clipboard

The cookies are valid for roughly 3 weeks (until the Garmin session
cookie expires).  When the GitHub Actions workflow starts returning 401
errors, re-run this script and update the secret.

Usage:
    python3 generate_cookies.py
"""
import base64
import os
import subprocess
import sys

COOKIE_FILE = "/tmp/garmin_cookies"

# Read from clipboard (macOS pbpaste) — avoids terminal paste limits
try:
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
    cookie_str = result.stdout.strip()
except (FileNotFoundError, subprocess.CalledProcessError):
    cookie_str = ""

if not cookie_str:
    print("Could not read from clipboard (pbpaste failed).")
    print("Paste the cookie value and press Ctrl+D when done:\n")
    try:
        cookie_str = sys.stdin.read().strip()
    except Exception:
        pass

if not cookie_str:
    print("ERROR: no cookie value received.")
    sys.exit(1)

# Basic sanity check
if "=" not in cookie_str or len(cookie_str) < 20:
    print(f"ERROR: clipboard contents don't look like a cookie string ({len(cookie_str)} chars).")
    print("Make sure you right-clicked the 'cookie:' header value and chose 'Copy value'.")
    sys.exit(1)

print(f"Cookies read from clipboard ({len(cookie_str)} chars).")

# Save raw cookie string to file
with open(COOKIE_FILE, "w") as f:
    f.write(cookie_str)
print(f"Saved to {COOKIE_FILE}")

# Show the gh command to store it as a secret
print("\nNow store it as a GitHub secret:")
print(f'  base64 -i {COOKIE_FILE} | tr -d "\\n" | gh secret set GARMIN_COOKIES --repo mearkus/scalesync')
