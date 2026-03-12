"""
Root conftest.py — sets required environment variables at module level so that
sync.py can be imported by test files without raising KeyError.
This must run before any test module imports sync.
"""
import os

os.environ.setdefault("WYZE_EMAIL", "test@example.com")
os.environ.setdefault("WYZE_PASSWORD", "testpassword")
os.environ.setdefault("WYZE_KEY_ID", "test-key-id")
os.environ.setdefault("WYZE_API_KEY", "test-api-key")
os.environ.setdefault("GARMIN_EMAIL", "garmin@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "garminpassword")
os.environ.setdefault("SYNC_INTERVAL", "30")
os.environ.setdefault("DATA_DIR", "/tmp/scalesync-test")
