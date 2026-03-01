"""
FIT file encoder for weight scale data.
Generates binary FIT files compatible with Garmin Connect.

FIT Protocol: https://developer.garmin.com/fit/protocol/
Adapted from: https://github.com/svanhoutte/wyze_garmin_sync
"""

import struct
from datetime import datetime, timezone

# FIT epoch: Jan 1, 1989 00:00:00 UTC
FIT_EPOCH = datetime(1989, 1, 1, tzinfo=timezone.utc)

# CRC lookup table for FIT protocol
CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def _crc_byte(crc, byte):
    """Update CRC with a single byte."""
    tmp = CRC_TABLE[crc & 0xF]
    crc = (crc >> 4) & 0x0FFF
    crc = crc ^ tmp ^ CRC_TABLE[byte & 0xF]
    tmp = CRC_TABLE[crc & 0xF]
    crc = (crc >> 4) & 0x0FFF
    crc = crc ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _calc_crc(data):
    """Calculate FIT CRC over bytes."""
    crc = 0
    for byte in data:
        crc = _crc_byte(crc, byte)
    return crc


def _unix_to_fit(unix_ts):
    """Convert Unix timestamp (seconds) to FIT timestamp."""
    epoch_unix = int(FIT_EPOCH.timestamp())
    fit_ts = unix_ts - epoch_unix
    return max(0, fit_ts)


class FitEncoder_Weight:
    """Encodes weight scale data into a valid FIT binary file."""

    # Message numbers
    MSG_FILE_ID = 0
    MSG_FILE_CREATOR = 49
    MSG_DEVICE_INFO = 23
    MSG_WEIGHT_SCALE = 30

    # Local message type numbers (0-indexed)
    LOCAL_MSG_FILE_ID = 0
    LOCAL_MSG_FILE_CREATOR = 1
    LOCAL_MSG_DEVICE_INFO = 2
    LOCAL_MSG_WEIGHT_SCALE = 3

    def __init__(self):
        self._body = bytearray()
        self._def_msgs = {}

    def _write_definition(self, local_msg_num, global_msg_num, fields):
        """
        Write a definition message.
        fields: list of (field_def_num, size, base_type)
        base_type codes: 0x84=uint16, 0x86=uint32, 0x88=sint32 scaled, 0x8A=uint32(special),
                         0x02=uint8, 0x07=string, 0x01=sint8
        """
        # Definition message header: bit 6 set = definition, bits 0-3 = local msg num
        header = 0x40 | (local_msg_num & 0x0F)
        num_fields = len(fields)
        # Architecture: 0 = little-endian
        defn = struct.pack(
            "<BBBHB",
            header,    # record header
            0,         # reserved
            0,         # architecture: little endian
            global_msg_num,
            num_fields,
        )
        for (fdef, fsize, ftype) in fields:
            defn += struct.pack("<BBB", fdef, fsize, ftype)
        self._body += defn
        self._def_msgs[local_msg_num] = fields

    def _write_data(self, local_msg_num, values):
        """Write a data message. values must match the field list from definition."""
        header = local_msg_num & 0x0F  # bit 6 clear = data message
        data = struct.pack("B", header)
        fields = self._def_msgs[local_msg_num]
        for i, (fdef, fsize, ftype) in enumerate(fields):
            val = values[i]
            if val is None:
                # Write "invalid" sentinel value for this field size
                data += b'\xFF' * fsize
            else:
                if fsize == 1:
                    data += struct.pack("<B", int(val) & 0xFF)
                elif fsize == 2:
                    data += struct.pack("<H", int(val) & 0xFFFF)
                elif fsize == 4:
                    data += struct.pack("<I", int(val) & 0xFFFFFFFF)
                else:
                    data += b'\xFF' * fsize
        self._body += data

    def write_file_info(self, time_created):
        """
        Write FILE_ID definition + data message.
        time_created: Unix timestamp (seconds)
        Fields: type(0), manufacturer(1), product(2), serial_number(3), time_created(4)
        """
        fields = [
            (0, 1, 0x02),   # type: uint8
            (1, 2, 0x84),   # manufacturer: uint16
            (2, 2, 0x84),   # product: uint16
            (3, 4, 0x86),   # serial_number: uint32z
            (4, 4, 0x86),   # time_created: uint32
        ]
        self._write_definition(self.LOCAL_MSG_FILE_ID, self.MSG_FILE_ID, fields)
        fit_ts = _unix_to_fit(int(time_created))
        self._write_data(self.LOCAL_MSG_FILE_ID, [
            9,       # type = 9 (weight scale)
            255,     # manufacturer = development/unknown
            0,       # product
            1,       # serial_number
            fit_ts,  # time_created
        ])

    def write_file_creator(self):
        """
        Write FILE_CREATOR definition + data message.
        Fields: software_version(0), hardware_version(1)
        """
        fields = [
            (0, 2, 0x84),   # software_version: uint16
            (1, 1, 0x02),   # hardware_version: uint8
        ]
        self._write_definition(self.LOCAL_MSG_FILE_CREATOR, self.MSG_FILE_CREATOR, fields)
        self._write_data(self.LOCAL_MSG_FILE_CREATOR, [100, 1])

    def write_device_info(self, timestamp):
        """
        Write DEVICE_INFO definition + data message.
        timestamp: Unix timestamp (seconds)
        Fields: timestamp(253), device_index(0), device_type(1), manufacturer(2),
                serial_number(3), product(4), software_version(5), hardware_version(6),
                battery_voltage(7), battery_status(8)
        """
        fields = [
            (253, 4, 0x86),  # timestamp: uint32
            (0,   1, 0x02),  # device_index: uint8
            (1,   1, 0x02),  # device_type: uint8
            (2,   2, 0x84),  # manufacturer: uint16
            (3,   4, 0x86),  # serial_number: uint32z
            (4,   2, 0x84),  # product: uint16
            (5,   2, 0x84),  # software_version: uint16
            (6,   1, 0x02),  # hardware_version: uint8
            (7,   2, 0x84),  # battery_voltage: uint16
            (8,   1, 0x02),  # battery_status: uint8
        ]
        self._write_definition(self.LOCAL_MSG_DEVICE_INFO, self.MSG_DEVICE_INFO, fields)
        fit_ts = _unix_to_fit(int(timestamp))
        self._write_data(self.LOCAL_MSG_DEVICE_INFO, [
            fit_ts, 0, 119, 255, 1, 0, 100, 1, None, None
        ])

    def write_weight_scale(
        self,
        timestamp,
        weight,
        percent_fat=None,
        percent_hydration=None,
        visceral_fat_mass=None,
        bone_mass=None,
        muscle_mass=None,
        basal_met=None,
        physique_rating=None,
        active_met=None,
        metabolic_age=None,
        visceral_fat_rating=None,
        bmi=None,
    ):
        """
        Write WEIGHT_SCALE definition + data message.

        FIT scale factors:
          weight:              100x (stored as uint16, e.g. 70.5 kg → 7050)
          percent_fat:         100x (e.g. 15.5% → 1550)
          percent_hydration:   100x
          visceral_fat_mass:   100x
          bone_mass:           100x
          muscle_mass:         100x
          basal_met:           4x  (e.g. 1800 kcal → 7200)
          physique_rating:     1x  (uint8)
          active_met:          4x
          metabolic_age:       1x  (uint8)
          visceral_fat_rating: 1x  (uint8)
          bmi:                 10x (e.g. 22.5 → 225)
        """
        fields = [
            (253, 4, 0x86),  # timestamp: uint32
            (0,   2, 0x84),  # weight: uint16 (scale 100)
            (1,   2, 0x84),  # percent_fat: uint16 (scale 100)
            (2,   2, 0x84),  # percent_hydration: uint16 (scale 100)
            (3,   2, 0x84),  # visceral_fat_mass: uint16 (scale 100)
            (4,   2, 0x84),  # bone_mass: uint16 (scale 100)
            (5,   2, 0x84),  # muscle_mass: uint16 (scale 100)
            (6,   2, 0x84),  # basal_met: uint16 (scale 4)
            (7,   1, 0x02),  # physique_rating: uint8
            (8,   2, 0x84),  # active_met: uint16 (scale 4)
            (9,   1, 0x02),  # metabolic_age: uint8
            (10,  1, 0x02),  # visceral_fat_rating: uint8
            (11,  2, 0x84),  # bmi: uint16 (scale 10)
        ]
        self._write_definition(self.LOCAL_MSG_WEIGHT_SCALE, self.MSG_WEIGHT_SCALE, fields)

        def _scale(val, factor):
            return int(round(val * factor)) if val is not None else None

        fit_ts = _unix_to_fit(int(timestamp))
        self._write_data(self.LOCAL_MSG_WEIGHT_SCALE, [
            fit_ts,
            _scale(weight, 100),
            _scale(percent_fat, 100),
            _scale(percent_hydration, 100),
            _scale(visceral_fat_mass, 100),
            _scale(bone_mass, 100),
            _scale(muscle_mass, 100),
            _scale(basal_met, 4),
            physique_rating,
            _scale(active_met, 4),
            metabolic_age,
            visceral_fat_rating,
            _scale(bmi, 10),
        ])

    def finish(self):
        """
        Return the complete FIT file as bytes (header + body + CRC).
        """
        body_bytes = bytes(self._body)
        body_size = len(body_bytes)

        # FIT file header: 14 bytes
        # [0]  header size = 14
        # [1]  protocol version = 16
        # [2-3] profile version = 2132 (little-endian)
        # [4-7] data size (little-endian)
        # [8-11] ".FIT" ASCII
        # [12-13] header CRC (little-endian)
        header = struct.pack(
            "<BBHIH",
            14,       # header_size
            16,       # protocol_version
            2132,     # profile_version (21.32)
            body_size,
            0,        # placeholder for header CRC
        ) + b".FIT"

        # Repack without CRC placeholder, then compute header CRC over first 12 bytes
        header_no_crc = struct.pack(
            "<BBHI",
            14,
            16,
            2132,
            body_size,
        ) + b".FIT"
        header_crc = _calc_crc(header_no_crc)
        header = header_no_crc + struct.pack("<H", header_crc)

        # Body CRC covers the entire body
        body_crc = _calc_crc(body_bytes)
        return header + body_bytes + struct.pack("<H", body_crc)
