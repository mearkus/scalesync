"""
Build Garmin OAuth token files from responses copied out of Chrome DevTools.

Steps:
  1. Open Chrome, go to connect.garmin.com and log in
  2. Open DevTools → Network tab, filter by "connectapi"
  3. Find the request to:
       /oauth-service/oauth/preauthorized?ticket=...
     Copy the full response body (looks like a URL query string:
       oauth_token=abc&oauth_token_secret=xyz&...)
  4. Find the request to:
       /oauth-service/oauth/exchange/user/2.0
     Copy the full response body (JSON)
  5. Paste each when prompted below

Usage (run in a real terminal):
    python3 generate_tokens.py
"""
import json
import os
import time
from urllib.parse import parse_qs

TOKEN_DIR = "/tmp/garmin_tokens"

print("=== Step 1: oauth/preauthorized response ===")
print("Paste the response body (URL query string), then press Enter twice:")
lines = []
while True:
    line = input()
    if line == "":
        break
    lines.append(line)
oauth1_raw = "".join(lines).strip()

print()
print("=== Step 2: oauth/exchange/user/2.0 response ===")
print("Paste the response body (JSON), then press Enter twice:")
lines = []
while True:
    line = input()
    if line == "":
        break
    lines.append(line)
oauth2_raw = "".join(lines).strip()

# --- Parse OAuth1 (URL query string) ---
parsed = {k: v[0] for k, v in parse_qs(oauth1_raw).items()}
oauth1 = {
    "oauth_token": parsed["oauth_token"],
    "oauth_token_secret": parsed["oauth_token_secret"],
    "domain": "garmin.com",
}
if "mfa_token" in parsed:
    oauth1["mfa_token"] = parsed["mfa_token"]
if "mfa_expiration_timestamp" in parsed:
    oauth1["mfa_expiration_timestamp"] = parsed["mfa_expiration_timestamp"]

# --- Parse OAuth2 (JSON) ---
oauth2 = json.loads(oauth2_raw)
# Add computed expiry timestamps if missing (garth requires them)
now = int(time.time())
if "expires_at" not in oauth2:
    oauth2["expires_at"] = now + oauth2["expires_in"]
if "refresh_token_expires_at" not in oauth2:
    oauth2["refresh_token_expires_at"] = now + oauth2["refresh_token_expires_in"]

# --- Save ---
os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
with open(f"{TOKEN_DIR}/oauth1_token.json", "w") as f:
    json.dump(oauth1, f, indent=4)
with open(f"{TOKEN_DIR}/oauth2_token.json", "w") as f:
    json.dump(oauth2, f, indent=4)

print(f"\nTokens saved to {TOKEN_DIR}/")
for name in sorted(os.listdir(TOKEN_DIR)):
    print(f"  {name}")

print("\nNow store them as GitHub secrets:")
print(f'  base64 -i {TOKEN_DIR}/oauth1_token.json | tr -d "\\n" | gh secret set GARMIN_OAUTH1_TOKEN --repo mearkus/scalesync')
print(f'  base64 -i {TOKEN_DIR}/oauth2_token.json | tr -d "\\n" | gh secret set GARMIN_OAUTH2_TOKEN --repo mearkus/scalesync')
