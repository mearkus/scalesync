# scalesync

Syncs body-composition measurements from a **Wyze Scale** to **Garmin Connect**.

Supports two deployment modes:

| Mode | Best for |
|------|----------|
| **GitHub Actions** | Fully automated, no server needed |
| **Docker** | Self-hosted, runs continuously |

---

## How it works

1. Authenticates with the Wyze API and fetches all scale records.
2. Maps each measurement to Garmin body-composition fields.
3. Uploads each measurement to Garmin Connect via `python-garminconnect`.
4. Tracks uploaded checksums in `data/synced.txt` so nothing is uploaded twice.

---

## Credentials you need

### Wyze

| Variable | Where to get it |
|----------|----------------|
| `WYZE_EMAIL` | Your Wyze account email |
| `WYZE_PASSWORD` | Your Wyze account password |
| `WYZE_KEY_ID` | [developer-api-console.wyze.com](https://developer-api-console.wyze.com) → API Keys |
| `WYZE_API_KEY` | Same page as above |

All four are required.

### Garmin Connect

| Variable | Where to get it |
|----------|----------------|
| `GARMIN_EMAIL` | Your Garmin Connect account email |
| `GARMIN_PASSWORD` | Your Garmin Connect account password |

> **Garmin MFA / IP block note**
> Garmin Connect may send a verification email or block logins from cloud IPs
> (like GitHub Actions runners) on the first attempt.
> If the workflow fails on its first run, log in once from your local machine
> using the same credentials — Garmin will then trust subsequent logins from
> new IPs for a period of time.
> After the first successful run, OAuth tokens are cached automatically and
> password-based login is not attempted again.

---

## Option A — GitHub Actions (recommended)

The included workflow (`.github/workflows/sync.yml`) runs every **6 hours**
and can also be triggered manually from the **Actions** tab.

### Setup

1. **Fork or push this repo to your GitHub account.**

2. **Add secrets** under *Settings → Secrets and variables → Actions → New repository secret*:

   - `WYZE_EMAIL`
   - `WYZE_PASSWORD`
   - `WYZE_KEY_ID`
   - `WYZE_API_KEY`
   - `GARMIN_EMAIL`
   - `GARMIN_PASSWORD`

3. **Enable Actions** on the repo (Actions tab → enable workflows).

4. **Trigger the first run manually** (Actions → *Sync Wyze Scale → Garmin Connect* → *Run workflow*).

That's it. Subsequent runs happen automatically every 6 hours.

### State persistence

Garmin OAuth tokens and the list of synced checksums are stored in the `data/`
directory and persisted between runs using GitHub Actions cache. The cache
expires after **7 days of inactivity** — if the workflow runs at least every
7 days (it runs every 6 hours by default), the cache will never expire.

---

## Option B — Docker

Runs as a long-lived container that syncs on a configurable interval.

### Setup

1. Copy the example env file and fill in your credentials:

   ```bash
   cp .env.example .env
   # edit .env
   ```

2. Start the container:

   ```bash
   docker compose up -d
   ```

The container persists OAuth tokens and sync state in `./data/` on your host.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_INTERVAL` | `30` | Minutes between sync runs |
| `DATA_DIR` | `/data` | Where to store tokens and synced.txt |

---

## Running locally (without Docker)

```bash
pip install -r requirements.txt

# Copy and fill in credentials
cp .env.example .env

# Load env and run once
set -a; source .env; set +a
python -c "from sync import sync_once; sync_once()"
```
