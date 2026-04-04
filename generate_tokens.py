"""
One-time script to generate Garmin OAuth tokens via a real browser.

Garmin's SSO endpoint blocks Python/curl requests (429) but allows real
browsers. This script opens Chromium, intercepts the OAuth token exchange
that happens during normal browser login, and saves the tokens in garth
format for use by the GitHub Actions workflow.

Usage (run in a real terminal, NOT via the ! prefix in Claude Code):
    python3 generate_tokens.py
"""
import asyncio
import json
import os
import sys

from garth.http import Client as GarthClient
from garth.auth_tokens import OAuth1Token, OAuth2Token
from garth import sso as garth_sso
from playwright.async_api import async_playwright

TOKEN_DIR = "/tmp/garmin_tokens"

# These are the connectapi.garmin.com endpoints the SSO flow exchanges
# tokens on. We intercept them at the browser network level, bypassing
# any Cloudflare bot detection that blocks Python requests.
OAUTH1_PATH = "/oauth-service/oauth/preauthorized"
OAUTH2_PATH = "/oauth-service/oauth/exchange/user/2.0"


async def main():
    oauth1_token: OAuth1Token | None = None
    oauth2_token: OAuth2Token | None = None
    token_event = asyncio.Event()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        async def on_response(response):
            nonlocal oauth1_token, oauth2_token
            try:
                if OAUTH1_PATH in response.url and response.status == 200:
                    from urllib.parse import parse_qs
                    text = await response.text()
                    parsed = {k: v[0] for k, v in parse_qs(text).items()}
                    oauth1_token = OAuth1Token(domain="garmin.com", **parsed)
                    print(f"  Captured OAuth1 token")

                elif OAUTH2_PATH in response.url and response.status == 200:
                    from garth.sso import set_expirations
                    data = await response.json()
                    oauth2_token = OAuth2Token(**set_expirations(data))
                    print(f"  Captured OAuth2 token")
            except Exception as e:
                print(f"  Warning: failed to parse response from {response.url}: {e}")

            if oauth1_token and oauth2_token:
                token_event.set()

        page.on("response", on_response)

        print("Opening Garmin Connect login page in browser...")
        print("Please log in with your Garmin credentials.\n")
        await page.goto("https://connect.garmin.com/signin")

        try:
            await asyncio.wait_for(token_event.wait(), timeout=180)
        except asyncio.TimeoutError:
            print("\nERROR: Timed out waiting for OAuth tokens.")
            print("Make sure you completed the login in the browser window.")
            await browser.close()
            sys.exit(1)

        await browser.close()

    # Save tokens using garth's native dump format
    print(f"\nSaving tokens to {TOKEN_DIR}/")
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)

    client = GarthClient()
    client.oauth1_token = oauth1_token
    client.oauth2_token = oauth2_token
    client.dump(TOKEN_DIR)

    for f in sorted(os.listdir(TOKEN_DIR)):
        print(f"  {f}")

    print("\nNow run the following to store them as GitHub secrets:")
    print(f'  base64 -i {TOKEN_DIR}/oauth1_token.json | tr -d "\\n" | gh secret set GARMIN_OAUTH1_TOKEN --repo mearkus/scalesync')
    print(f'  base64 -i {TOKEN_DIR}/oauth2_token.json | tr -d "\\n" | gh secret set GARMIN_OAUTH2_TOKEN --repo mearkus/scalesync')


if __name__ == "__main__":
    asyncio.run(main())
