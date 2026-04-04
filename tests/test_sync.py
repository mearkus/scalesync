"""Tests for sync.py."""
import importlib
import os
import time
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from garminconnect import GarminConnectConnectionError, GarminConnectTooManyRequestsError

import sync


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "WYZE_EMAIL": "test@example.com",
    "WYZE_PASSWORD": "testpassword",
    "WYZE_KEY_ID": "test-key-id",
    "WYZE_API_KEY": "test-api-key",
    "GARMIN_EMAIL": "garmin@example.com",
    "GARMIN_PASSWORD": "garminpassword",
    "SYNC_INTERVAL": "30",
    "DATA_DIR": "/tmp/scalesync-test",
}


def _reload(env_overrides=None):
    """Reload sync with a controlled environment and return the module."""
    env = {**_BASE_ENV, **(env_overrides or {})}
    with patch.dict(os.environ, env, clear=True):
        return importlib.reload(sync)


def _make_device(**kwargs):
    d = MagicMock()
    d.type = kwargs.get("type", "WyzeScale")
    d.mac = kwargs.get("mac", "AA:BB:CC:DD:EE:FF")
    d.nickname = kwargs.get("nickname", "My Scale")
    d.product_model = kwargs.get("product_model", "WL_SC2")
    d.product_type = kwargs.get("product_type", "scale")
    return d


def _make_record(**kwargs):
    r = MagicMock()
    r.measure_ts = kwargs.get("measure_ts", 1704067200000)  # 2024-01-01 00:00 UTC
    r.weight = kwargs.get("weight", 154.0)
    r.body_fat = kwargs.get("body_fat", 20.0)
    r.body_water = kwargs.get("body_water", 55.0)
    r.body_vfr = kwargs.get("body_vfr", 5.0)
    r.bone_mineral = kwargs.get("bone_mineral", 3.0)
    r.muscle = kwargs.get("muscle", 60.0)
    r.bmr = kwargs.get("bmr", 1800.0)
    r.metabolic_age = kwargs.get("metabolic_age", 30)
    r.body_type = kwargs.get("body_type", 3)
    r.bmi = kwargs.get("bmi", 22.5)
    return r


# ---------------------------------------------------------------------------
# resolve_date_range
# ---------------------------------------------------------------------------

class TestResolveDateRange:
    def test_no_dates_returns_today(self):
        with patch.object(sync, "DATE_FROM", ""), patch.object(sync, "DATE_TO", ""):
            start, end = sync.resolve_date_range()
        today = datetime.now().date()
        assert start == today
        assert end == today

    def test_date_from_only(self):
        with patch.object(sync, "DATE_FROM", "2024-01-15"), patch.object(sync, "DATE_TO", ""):
            start, end = sync.resolve_date_range()
        assert start == date(2024, 1, 15)
        assert end == date(2024, 1, 15)

    def test_date_to_only(self):
        with patch.object(sync, "DATE_FROM", ""), patch.object(sync, "DATE_TO", "2024-01-20"):
            start, end = sync.resolve_date_range()
        assert start == date(2024, 1, 20)
        assert end == date(2024, 1, 20)

    def test_both_dates(self):
        with patch.object(sync, "DATE_FROM", "2024-01-01"), patch.object(sync, "DATE_TO", "2024-01-31"):
            start, end = sync.resolve_date_range()
        assert start == date(2024, 1, 1)
        assert end == date(2024, 1, 31)

    def test_invalid_date_format_raises(self):
        with patch.object(sync, "DATE_FROM", "01/15/2024"), patch.object(sync, "DATE_TO", ""):
            with pytest.raises(ValueError, match="Invalid date format"):
                sync.resolve_date_range()

    def test_date_from_after_date_to_raises(self):
        with patch.object(sync, "DATE_FROM", "2024-01-31"), patch.object(sync, "DATE_TO", "2024-01-01"):
            with pytest.raises(ValueError, match="cannot be after"):
                sync.resolve_date_range()

    def test_same_start_and_end_allowed(self):
        with patch.object(sync, "DATE_FROM", "2024-06-15"), patch.object(sync, "DATE_TO", "2024-06-15"):
            start, end = sync.resolve_date_range()
        assert start == end == date(2024, 6, 15)


# ---------------------------------------------------------------------------
# load_synced / mark_synced
# ---------------------------------------------------------------------------

class TestLoadSynced:
    def test_missing_file_returns_empty_set(self, tmp_path):
        with patch.object(sync, "SYNCED_FILE", str(tmp_path / "nonexistent.txt")):
            assert sync.load_synced() == set()

    def test_loads_checksums(self, tmp_path):
        f = tmp_path / "synced.txt"
        f.write_text("abc123\ndef456\n")
        with patch.object(sync, "SYNCED_FILE", str(f)):
            assert sync.load_synced() == {"abc123", "def456"}

    def test_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "synced.txt"
        f.write_text("abc123\n\n   \ndef456\n")
        with patch.object(sync, "SYNCED_FILE", str(f)):
            assert sync.load_synced() == {"abc123", "def456"}

    def test_empty_file_returns_empty_set(self, tmp_path):
        f = tmp_path / "synced.txt"
        f.write_text("")
        with patch.object(sync, "SYNCED_FILE", str(f)):
            assert sync.load_synced() == set()


class TestMarkSynced:
    def test_appends_checksum(self, tmp_path):
        synced_file = tmp_path / "synced.txt"
        with patch.object(sync, "SYNCED_FILE", str(synced_file)), \
             patch.object(sync, "DATA_DIR", str(tmp_path)):
            sync.mark_synced("abc123")
            sync.mark_synced("def456")
        lines = synced_file.read_text().splitlines()
        assert lines == ["abc123", "def456"]

    def test_creates_dir_if_missing(self, tmp_path):
        data_dir = tmp_path / "newdir"
        synced_file = data_dir / "synced.txt"
        with patch.object(sync, "SYNCED_FILE", str(synced_file)), \
             patch.object(sync, "DATA_DIR", str(data_dir)):
            sync.mark_synced("abc123")
        assert synced_file.exists()

    def test_appends_to_existing_file(self, tmp_path):
        synced_file = tmp_path / "synced.txt"
        synced_file.write_text("existing\n")
        with patch.object(sync, "SYNCED_FILE", str(synced_file)), \
             patch.object(sync, "DATA_DIR", str(tmp_path)):
            sync.mark_synced("new")
        assert "existing" in synced_file.read_text()
        assert "new" in synced_file.read_text()


# ---------------------------------------------------------------------------
# checksum_payload
# ---------------------------------------------------------------------------

class TestChecksumPayload:
    def test_returns_sha256_hex_length(self):
        payload = {"weight": 70.0, "timestamp": "2024-01-01T00:00:00.000+00:00"}
        result = sync.checksum_payload(payload)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_payload_same_checksum(self):
        payload = {"weight": 70.0, "timestamp": "2024-01-01T00:00:00.000+00:00"}
        assert sync.checksum_payload(payload) == sync.checksum_payload(payload)

    def test_different_payloads_different_checksums(self):
        assert sync.checksum_payload({"weight": 70.0}) != sync.checksum_payload({"weight": 71.0})

    def test_key_order_does_not_matter(self):
        p1 = {"a": 1, "b": 2}
        p2 = {"b": 2, "a": 1}
        assert sync.checksum_payload(p1) == sync.checksum_payload(p2)


# ---------------------------------------------------------------------------
# _float / _int
# ---------------------------------------------------------------------------

class TestFloatInt:
    def test_float_none(self):
        assert sync._float(None) is None

    def test_float_string(self):
        assert sync._float("70.5") == 70.5

    def test_float_int(self):
        assert sync._float(70) == 70.0

    def test_int_none(self):
        assert sync._int(None) is None

    def test_int_string(self):
        assert sync._int("5") == 5

    def test_int_truncates_float(self):
        assert sync._int(5.9) == 5


# ---------------------------------------------------------------------------
# _record_payload
# ---------------------------------------------------------------------------

class TestRecordPayload:
    def test_weight_lbs_to_kg_conversion(self):
        record = _make_record(weight=220.462)
        payload = sync._record_payload(record)
        assert abs(payload["weight"] - 100.0) < 0.01

    def test_timestamp_is_utc_iso(self):
        record = _make_record(measure_ts=1704067200000)
        payload = sync._record_payload(record)
        assert payload["timestamp"].endswith("+00:00")

    def test_timestamp_correct_date(self):
        record = _make_record(measure_ts=1704067200000)  # 2024-01-01 00:00:00 UTC
        payload = sync._record_payload(record)
        assert "2024-01-01" in payload["timestamp"]

    def test_none_weight_gives_none(self):
        record = _make_record(weight=None)
        payload = sync._record_payload(record)
        assert payload["weight"] is None

    def test_bmr_calculations(self):
        record = _make_record(bmr=1800.0)
        payload = sync._record_payload(record)
        assert payload["basal_met"] == 1800
        assert payload["active_met"] == 2250  # 1800 * 1.25

    def test_none_bmr_gives_none_for_both_met_fields(self):
        record = _make_record(bmr=None)
        payload = sync._record_payload(record)
        assert payload["basal_met"] is None
        assert payload["active_met"] is None

    def test_payload_keys(self):
        record = _make_record()
        payload = sync._record_payload(record)
        expected_keys = {
            "timestamp", "weight", "percent_fat", "percent_hydration",
            "visceral_fat_mass", "bone_mass", "muscle_mass", "basal_met",
            "active_met", "physique_rating", "metabolic_age",
            "visceral_fat_rating", "bmi",
        }
        assert set(payload.keys()) == expected_keys


# ---------------------------------------------------------------------------
# SYNC_INTERVAL validation (via module reload)
# ---------------------------------------------------------------------------

class TestSyncInterval:
    @pytest.fixture(autouse=True)
    def restore_sync(self):
        yield
        _reload()  # restore module to default state after each test

    def test_valid_interval(self):
        mod = _reload({"SYNC_INTERVAL": "60"})
        assert mod.SYNC_INTERVAL == 60

    def test_boundary_min(self):
        mod = _reload({"SYNC_INTERVAL": "1"})
        assert mod.SYNC_INTERVAL == 1

    def test_boundary_max(self):
        mod = _reload({"SYNC_INTERVAL": "1440"})
        assert mod.SYNC_INTERVAL == 1440

    def test_non_integer_raises(self):
        with pytest.raises(ValueError, match="must be an integer"):
            _reload({"SYNC_INTERVAL": "abc"})

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="between 1 and 1440"):
            _reload({"SYNC_INTERVAL": "0"})

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="between 1 and 1440"):
            _reload({"SYNC_INTERVAL": "-5"})

    def test_too_large_raises(self):
        with pytest.raises(ValueError, match="between 1 and 1440"):
            _reload({"SYNC_INTERVAL": "1441"})


# ---------------------------------------------------------------------------
# DATA_DIR path normalization (via module reload)
# ---------------------------------------------------------------------------

class TestDataDir:
    @pytest.fixture(autouse=True)
    def restore_sync(self):
        yield
        _reload()

    def test_resolves_to_absolute_path(self):
        mod = _reload({"DATA_DIR": "/data"})
        assert os.path.isabs(mod.DATA_DIR)

    def test_normalizes_dotdot(self):
        mod = _reload({"DATA_DIR": "/data/../data"})
        assert ".." not in mod.DATA_DIR

    def test_path_traversal_is_resolved(self):
        mod = _reload({"DATA_DIR": "/tmp/scalesync/../../tmp/scalesync"})
        assert ".." not in mod.DATA_DIR
        assert os.path.isabs(mod.DATA_DIR)

    def test_garmin_tokens_dir_is_subpath(self):
        mod = _reload({"DATA_DIR": "/tmp/scalesync-test"})
        assert mod.GARMIN_TOKENS_DIR.startswith(mod.DATA_DIR)

    def test_synced_file_is_subpath(self):
        mod = _reload({"DATA_DIR": "/tmp/scalesync-test"})
        assert mod.SYNCED_FILE.startswith(mod.DATA_DIR)


# ---------------------------------------------------------------------------
# sync_once (with all external calls mocked)
# ---------------------------------------------------------------------------

class TestSyncOnce:
    """Integration-style tests for sync_once() with external calls mocked."""

    def _setup_client(self, mock_client_cls, devices=None, scale_records=None):
        mock_client = mock_client_cls.return_value
        mock_client.devices_list.return_value = devices or []
        if scale_records is not None:
            mock_scale = MagicMock()
            mock_scale.latest_records = scale_records
            mock_client.scales.info.return_value = mock_scale
        return mock_client

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_returns_zero_when_no_devices(self, mock_client_cls, *_):
        self._setup_client(mock_client_cls, devices=[])
        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            assert sync.sync_once() == 0

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_uploads_record_and_returns_count(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            result = sync.sync_once()

        assert result == 1
        mock_garmin.return_value.add_body_composition.assert_called_once()
        mock_mark.assert_called_once()

    @patch("sync.mark_synced")
    @patch("sync.load_synced")
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_skips_already_synced_record(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])

        payload = sync._record_payload(record)
        mock_load.return_value = {sync.checksum_payload(payload)}

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            result = sync.sync_once()

        assert result == 0
        mock_garmin.return_value.add_body_composition.assert_not_called()
        mock_mark.assert_not_called()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_dry_run_logs_without_uploading(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])

        with patch.object(sync, "DRY_RUN", True), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            result = sync.sync_once()

        assert result == 1
        mock_garmin.return_value.add_body_composition.assert_not_called()
        mock_mark.assert_not_called()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_skips_record_outside_date_window(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record(measure_ts=1704067200000)  # 2024-01-01
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-02"), \
             patch.object(sync, "DATE_TO", "2024-01-02"):
            result = sync.sync_once()

        assert result == 0
        mock_garmin.return_value.add_body_composition.assert_not_called()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_skips_record_with_no_weight(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record(weight=None)
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            result = sync.sync_once()

        assert result == 0
        mock_garmin.return_value.add_body_composition.assert_not_called()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_multiple_records_uploaded(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        records = [
            _make_record(measure_ts=1704067200000, weight=154.0),  # 2024-01-01
            _make_record(measure_ts=1704153600000, weight=153.0),  # 2024-01-02
        ]
        self._setup_client(mock_client_cls, devices=[device], scale_records=records)

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-02"):
            result = sync.sync_once()

        assert result == 2
        assert mock_garmin.return_value.add_body_composition.call_count == 2
        assert mock_mark.call_count == 2

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_garmin_upload_failure_does_not_crash(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])
        mock_garmin.return_value.add_body_composition.side_effect = RuntimeError("API error")

        with patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            result = sync.sync_once()

        assert result == 0  # upload failed, nothing marked synced
        mock_mark.assert_not_called()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_upload_429_writes_backoff_and_raises(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark, tmp_path):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])
        mock_garmin.return_value.add_body_composition.side_effect = \
            GarminConnectTooManyRequestsError()

        backoff_file = tmp_path / "backoff"
        with patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            with pytest.raises(RuntimeError, match="rate-limited during upload"):
                sync.sync_once()

        assert backoff_file.exists()
        assert float(backoff_file.read_text()) > time.time()

    @patch("sync.mark_synced")
    @patch("sync.load_synced", return_value=set())
    @patch("sync.garmin_auth")
    @patch("sync.wyze_auth", return_value="fake-token")
    @patch("sync.Client")
    def test_upload_429_via_connection_error_writes_backoff(self, mock_client_cls, mock_wyze, mock_garmin, mock_load, mock_mark, tmp_path):
        device = _make_device()
        record = _make_record()
        self._setup_client(mock_client_cls, devices=[device], scale_records=[record])
        mock_garmin.return_value.add_body_composition.side_effect = \
            GarminConnectConnectionError("Error: 429 Too Many Requests")

        backoff_file = tmp_path / "backoff"
        with patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch.object(sync, "DRY_RUN", False), \
             patch.object(sync, "DATE_FROM", "2024-01-01"), \
             patch.object(sync, "DATE_TO", "2024-01-01"):
            with pytest.raises(RuntimeError, match="rate-limited during upload"):
                sync.sync_once()

        assert backoff_file.exists()


# ---------------------------------------------------------------------------
# _is_garmin_rate_limit
# ---------------------------------------------------------------------------

class TestIsGarminRateLimit:
    def test_true_for_too_many_requests_error(self):
        assert sync._is_garmin_rate_limit(GarminConnectTooManyRequestsError())

    def test_true_for_connection_error_with_429(self):
        exc = GarminConnectConnectionError("Login failed: 429 Too Many Requests")
        assert sync._is_garmin_rate_limit(exc)

    def test_false_for_connection_error_without_429(self):
        exc = GarminConnectConnectionError("Login failed: connection refused")
        assert not sync._is_garmin_rate_limit(exc)

    def test_false_for_generic_exception(self):
        assert not sync._is_garmin_rate_limit(RuntimeError("something else"))

    def test_true_for_runtime_error_mentioning_429(self):
        # garth can surface 429 as a plain RuntimeError in some paths
        assert sync._is_garmin_rate_limit(RuntimeError("HTTP 429 Too Many Requests"))


# ---------------------------------------------------------------------------
# _write_garmin_backoff
# ---------------------------------------------------------------------------

class TestWriteGarminBackoff:
    def test_writes_future_timestamp(self, tmp_path):
        backoff_file = tmp_path / "garmin_backoff"
        before = time.time()
        with patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)):
            sync._write_garmin_backoff()
        after = time.time()
        ts = float(backoff_file.read_text())
        assert ts > before
        assert ts <= after + sync._GARMIN_BACKOFF_SECONDS + 1

    def test_timestamp_is_approximately_26h_from_now(self, tmp_path):
        backoff_file = tmp_path / "garmin_backoff"
        before = time.time()
        with patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)):
            sync._write_garmin_backoff()
        ts = float(backoff_file.read_text())
        # Allow 5 s of slop
        assert abs(ts - (before + sync._GARMIN_BACKOFF_SECONDS)) < 5

    def test_silently_ignores_os_error(self, tmp_path):
        with patch.object(sync, "GARMIN_BACKOFF_FILE", "/nonexistent/dir/backoff"):
            sync._write_garmin_backoff()  # should not raise


# ---------------------------------------------------------------------------
# garmin_auth
# ---------------------------------------------------------------------------

class TestGarminAuth:
    def _seed_tokens(self, token_dir):
        """Create a minimal oauth1_token.json so garmin_auth passes the token check."""
        token_dir.mkdir(exist_ok=True)
        (token_dir / "oauth1_token.json").write_text("{}")

    def test_sets_token_dir_permissions(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.return_value = None
            sync.garmin_auth()
        assert oct(token_dir.stat().st_mode)[-3:] == "700"

    def test_raises_runtime_error_on_failure(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.side_effect = Exception("auth failed")
            with pytest.raises(RuntimeError, match="Garmin authentication failed"):
                sync.garmin_auth()

    # --- backoff file ---

    def test_raises_backoff_when_file_active(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        backoff_file = tmp_path / "backoff"
        backoff_file.write_text(str(time.time() + 3600))  # 1 h from now
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            with pytest.raises(sync.GarminRateLimitBackoff, match="rate-limit backoff"):
                sync.garmin_auth()
        mock_garmin_cls.assert_not_called()  # no network attempt

    def test_proceeds_when_backoff_file_expired(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        backoff_file.write_text(str(time.time() - 1))  # 1 s in the past
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.return_value = None
            sync.garmin_auth()  # should not raise

    def test_proceeds_when_backoff_file_corrupt(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        backoff_file.write_text("not-a-number")
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.return_value = None
            sync.garmin_auth()  # corrupt file → attempt auth anyway

    # --- missing tokens ---

    def test_raises_when_no_token_file(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(tmp_path / "backoff")), \
             patch("sync.Garmin") as mock_garmin_cls:
            with pytest.raises(RuntimeError, match="No Garmin OAuth tokens found"):
                sync.garmin_auth()
        mock_garmin_cls.assert_not_called()

    # --- warm-start (cached tokens exist) ---

    def test_warm_start_calls_login_with_tokenstore(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        token_dir.mkdir()
        (token_dir / "oauth1_token.json").write_text("{}")
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(tmp_path / "backoff")), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_client = mock_garmin_cls.return_value
            mock_client.login.return_value = None
            sync.garmin_auth()
        mock_client.login.assert_called_once_with(tokenstore=str(token_dir))

    def test_warm_start_does_not_dump_tokens(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        token_dir.mkdir()
        (token_dir / "oauth1_token.json").write_text("{}")
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(tmp_path / "backoff")), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_client = mock_garmin_cls.return_value
            mock_client.login.return_value = None
            sync.garmin_auth()
        mock_client.garth.dump.assert_not_called()

    # --- 429 handling ---

    def test_429_via_too_many_requests_writes_backoff(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.side_effect = GarminConnectTooManyRequestsError()
            with pytest.raises(RuntimeError, match="rate-limited"):
                sync.garmin_auth()
        assert backoff_file.exists()
        assert float(backoff_file.read_text()) > time.time()

    def test_429_via_connection_error_writes_backoff(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            exc = GarminConnectConnectionError("Login failed: 429 Too Many Requests")
            mock_garmin_cls.return_value.login.side_effect = exc
            with pytest.raises(RuntimeError, match="rate-limited"):
                sync.garmin_auth()
        assert backoff_file.exists()

    def test_non_429_connection_error_does_not_write_backoff(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            exc = GarminConnectConnectionError("connection refused")
            mock_garmin_cls.return_value.login.side_effect = exc
            with pytest.raises(RuntimeError, match="Garmin authentication failed"):
                sync.garmin_auth()
        assert not backoff_file.exists()

    def test_success_clears_existing_backoff_file(self, tmp_path):
        token_dir = tmp_path / "garmin_tokens"
        self._seed_tokens(token_dir)
        backoff_file = tmp_path / "backoff"
        backoff_file.write_text(str(time.time() - 1))  # expired backoff
        with patch.object(sync, "GARMIN_TOKENS_DIR", str(token_dir)), \
             patch.object(sync, "GARMIN_BACKOFF_FILE", str(backoff_file)), \
             patch("sync.Garmin") as mock_garmin_cls:
            mock_garmin_cls.return_value.login.return_value = None
            sync.garmin_auth()
        assert not backoff_file.exists()


# ---------------------------------------------------------------------------
# wyze_auth
# ---------------------------------------------------------------------------

class TestWyzeAuth:
    def test_returns_access_token(self):
        with patch("sync.Client") as mock_client_cls:
            mock_client_cls.return_value.login.return_value = {"access_token": "tok123"}
            token = sync.wyze_auth()
        assert token == "tok123"

    def test_raises_when_no_token_returned(self):
        with patch("sync.Client") as mock_client_cls:
            mock_client_cls.return_value.login.return_value = {}
            with pytest.raises(RuntimeError, match="no access_token"):
                sync.wyze_auth()
