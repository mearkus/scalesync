"""Tests for fit.py — FIT file encoder."""
import struct
from datetime import datetime, timezone

import pytest

from fit import FIT_EPOCH, FitEncoder_Weight, _calc_crc, _crc_byte, _unix_to_fit


# ---------------------------------------------------------------------------
# _crc_byte
# ---------------------------------------------------------------------------

class TestCrcByte:
    def test_returns_int(self):
        assert isinstance(_crc_byte(0, 0), int)

    def test_result_is_16_bit(self):
        for byte in range(256):
            assert 0 <= _crc_byte(0, byte) <= 0xFFFF

    def test_deterministic(self):
        assert _crc_byte(0x1234, 0xAB) == _crc_byte(0x1234, 0xAB)

    def test_different_bytes_different_results(self):
        assert _crc_byte(0, 0x01) != _crc_byte(0, 0x02)

    def test_different_crcs_different_results(self):
        assert _crc_byte(0x0001, 0xFF) != _crc_byte(0x0002, 0xFF)


# ---------------------------------------------------------------------------
# _calc_crc
# ---------------------------------------------------------------------------

class TestCalcCrc:
    def test_empty_data_is_zero(self):
        assert _calc_crc(b"") == 0

    def test_result_is_16_bit(self):
        assert 0 <= _calc_crc(b"hello world" * 100) <= 0xFFFF

    def test_deterministic(self):
        data = b"scalesync test data"
        assert _calc_crc(data) == _calc_crc(data)

    def test_different_data_different_crc(self):
        assert _calc_crc(b"hello") != _calc_crc(b"world")

    def test_single_zero_byte(self):
        result = _calc_crc(b"\x00")
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF

    def test_all_0xff_bytes(self):
        result = _calc_crc(b"\xFF" * 16)
        assert 0 <= result <= 0xFFFF


# ---------------------------------------------------------------------------
# _unix_to_fit
# ---------------------------------------------------------------------------

class TestUnixToFit:
    def test_fit_epoch_converts_to_zero(self):
        assert _unix_to_fit(int(FIT_EPOCH.timestamp())) == 0

    def test_before_fit_epoch_clamped_to_zero(self):
        assert _unix_to_fit(int(FIT_EPOCH.timestamp()) - 1000) == 0

    def test_one_second_after_fit_epoch(self):
        assert _unix_to_fit(int(FIT_EPOCH.timestamp()) + 1) == 1

    def test_one_hour_after_fit_epoch(self):
        assert _unix_to_fit(int(FIT_EPOCH.timestamp()) + 3600) == 3600

    def test_known_unix_timestamp(self):
        # 2024-01-01 00:00:00 UTC
        unix_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
        fit_epoch_unix = int(FIT_EPOCH.timestamp())
        assert _unix_to_fit(unix_ts) == unix_ts - fit_epoch_unix

    def test_fit_epoch_is_1989(self):
        assert FIT_EPOCH.year == 1989
        assert FIT_EPOCH.month == 1
        assert FIT_EPOCH.day == 1


# ---------------------------------------------------------------------------
# FitEncoder_Weight
# ---------------------------------------------------------------------------

SAMPLE_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())


def _full_encode(**weight_scale_kwargs):
    """Build a complete FIT file with all message types."""
    enc = FitEncoder_Weight()
    enc.write_file_info(SAMPLE_TS)
    enc.write_file_creator()
    enc.write_device_info(SAMPLE_TS)
    kw = {"timestamp": SAMPLE_TS, "weight": 70.5}
    kw.update(weight_scale_kwargs)
    enc.write_weight_scale(**kw)
    return enc.finish()


class TestFitFileStructure:
    def test_finish_returns_bytes(self):
        assert isinstance(_full_encode(), bytes)

    def test_fit_signature_at_bytes_8_to_12(self):
        data = _full_encode()
        assert data[8:12] == b".FIT"

    def test_header_size_byte_is_14(self):
        assert _full_encode()[0] == 14

    def test_protocol_version_byte_is_16(self):
        assert _full_encode()[1] == 16

    def test_total_length_matches_header_body_size(self):
        data = _full_encode()
        body_size = struct.unpack("<I", data[4:8])[0]
        assert len(data) == 14 + body_size + 2  # header + body + body CRC

    def test_minimum_length(self):
        # 14 header + at least 1 byte body + 2 CRC
        assert len(_full_encode()) > 17

    def test_header_crc_is_nonzero(self):
        data = _full_encode()
        header_crc = struct.unpack("<H", data[12:14])[0]
        assert header_crc != 0


class TestFitEncoderWeightScale:
    def test_with_all_optional_fields(self):
        data = _full_encode(
            percent_fat=20.0,
            percent_hydration=55.0,
            visceral_fat_mass=5.0,
            bone_mass=3.0,
            muscle_mass=60.0,
            basal_met=1800,
            physique_rating=3,
            active_met=2250,
            metabolic_age=30,
            visceral_fat_rating=5,
            bmi=22.5,
        )
        assert data[8:12] == b".FIT"

    def test_with_none_optional_fields(self):
        data = _full_encode(
            percent_fat=None,
            percent_hydration=None,
            visceral_fat_mass=None,
            bone_mass=None,
            muscle_mass=None,
        )
        assert data[8:12] == b".FIT"

    def test_none_weight_writes_sentinel(self):
        enc = FitEncoder_Weight()
        enc.write_file_info(SAMPLE_TS)
        enc.write_weight_scale(timestamp=SAMPLE_TS, weight=None)
        data = enc.finish()
        assert isinstance(data, bytes)

    def test_multiple_weight_scale_records(self):
        enc = FitEncoder_Weight()
        enc.write_file_info(SAMPLE_TS)
        enc.write_file_creator()
        enc.write_device_info(SAMPLE_TS)
        enc.write_weight_scale(timestamp=SAMPLE_TS, weight=70.0)
        enc.write_weight_scale(timestamp=SAMPLE_TS + 86400, weight=69.5)
        data = enc.finish()
        assert data[8:12] == b".FIT"

    def test_different_weights_produce_different_files(self):
        data1 = _full_encode(weight=70.0)
        data2 = _full_encode(weight=80.0)
        assert data1 != data2

    def test_empty_encoder_finish(self):
        enc = FitEncoder_Weight()
        data = enc.finish()
        # Just header (14 bytes) + empty body + 2-byte CRC
        assert len(data) == 16
        assert data[8:12] == b".FIT"


class TestFitEncoderFileInfo:
    def test_write_file_info_adds_to_body(self):
        enc = FitEncoder_Weight()
        enc.write_file_info(SAMPLE_TS)
        data = enc.finish()
        body_size = struct.unpack("<I", data[4:8])[0]
        assert body_size > 0

    def test_write_file_creator_adds_to_body(self):
        enc1 = FitEncoder_Weight()
        enc1.write_file_info(SAMPLE_TS)
        size1 = struct.unpack("<I", enc1.finish()[4:8])[0]

        enc2 = FitEncoder_Weight()
        enc2.write_file_info(SAMPLE_TS)
        enc2.write_file_creator()
        size2 = struct.unpack("<I", enc2.finish()[4:8])[0]

        assert size2 > size1

    def test_write_device_info_adds_to_body(self):
        enc1 = FitEncoder_Weight()
        enc1.write_file_info(SAMPLE_TS)
        enc1.write_file_creator()
        size1 = struct.unpack("<I", enc1.finish()[4:8])[0]

        enc2 = FitEncoder_Weight()
        enc2.write_file_info(SAMPLE_TS)
        enc2.write_file_creator()
        enc2.write_device_info(SAMPLE_TS)
        size2 = struct.unpack("<I", enc2.finish()[4:8])[0]

        assert size2 > size1
