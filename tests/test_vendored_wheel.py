"""Tests for the vendored garminconnect wheel.

requirements.txt installs vendor/garminconnect-*.whl rather than building the
source next to it, so that no build backend has to be downloaded at install
time. That leaves two copies of the same code in the tree, so these tests
guard against them drifting apart. If they fail, rebuild the wheel:

    pip wheel --no-deps -w vendor/ ./vendor/python-garminconnect
"""
import hashlib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
SOURCE_DIR = VENDOR_DIR / "python-garminconnect"


def _wheel_path():
    wheels = sorted(VENDOR_DIR.glob("garminconnect-*.whl"))
    assert len(wheels) == 1, f"expected exactly one vendored wheel, found {wheels}"
    return wheels[0]


def _wheel_modules():
    with zipfile.ZipFile(_wheel_path()) as zf:
        return {n: zf.read(n) for n in zf.namelist() if n.endswith(".py")}


class TestVendoredWheel:
    def test_wheel_is_present(self):
        assert _wheel_path().is_file()

    def test_wheel_is_referenced_by_requirements(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text()
        assert _wheel_path().name in requirements

    def test_wheel_ships_the_package(self):
        assert "garminconnect/__init__.py" in _wheel_modules()

    @pytest.mark.parametrize(
        "module",
        ["__init__.py", "client.py", "exceptions.py", "fit.py", "workout.py"],
    )
    def test_module_matches_vendored_source(self, module):
        """Each module in the wheel is byte-for-byte the vendored source."""
        packaged = _wheel_modules()[f"garminconnect/{module}"]
        source = (SOURCE_DIR / "garminconnect" / module).read_bytes()
        assert hashlib.sha256(packaged).hexdigest() == hashlib.sha256(source).hexdigest()

    def test_no_source_module_is_missing_from_the_wheel(self):
        packaged = {n.split("/", 1)[1] for n in _wheel_modules()}
        on_disk = {p.name for p in (SOURCE_DIR / "garminconnect").glob("*.py")}
        assert on_disk == packaged
