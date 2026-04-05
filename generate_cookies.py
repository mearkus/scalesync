"""
Capture Garmin Connect browser session cookies for use as a GitHub secret.

Garmin's OAuth SSO has blocked automated clients since March 2026.  This
script lets you grab the cookies from a live browser session and package
them for storage as the GARMIN_COOKIES GitHub Actions secret.

Steps:
  1. Open Chrome and log in to connect.garmin.com
  2. Open DevTools (F12) → Network tab
  3. Click any request to connect.garmin.com or connectapi.garmin.com
  4. Under "Request Headers", find the "cookie:" header
  5. Copy the entire value (the long string after "cookie: ")
  6. Paste it when prompted below

The cookies are valid for roughly 3 weeks (until the Garmin session
cookie expires).  When the GitHub Actions workflow starts returning 401
errors, re-run this script and update the secret.

Usage (run in a real terminal, NOT via Claude Code's ! prefix):
    python3 generate_cookies.py
"""
import base64
import os
import subprocess
import sys

COOKIE_FILE = "/tmp/garmin_cookies"

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
    print("ERROR: no input received.")
    sys.exit(1)

print(f"\nCookies received ({len(cookie_str)} chars).")

# Save raw cookie string to file
os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
with open(COOKIE_FILE, "w") as f:
    f.write(cookie_str)

print(f"Saved to {COOKIE_FILE}")

# Show the base64-encoded value and the gh command to store it
encoded = base64.b64encode(cookie_str.encode()).decode()
print(f"\nBase64-encoded length: {len(encoded)} chars")
print("\nNow store it as a GitHub secret:")
print(f'  base64 -i {COOKIE_FILE} | tr -d "\\n" | gh secret set GARMIN_COOKIES --repo mearkus/scalesync')
print()
print("Or copy this value and paste it manually in GitHub → Settings → Secrets:")
print(encoded[:80] + ("..." if len(encoded) > 80 else ""))
