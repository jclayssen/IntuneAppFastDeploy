import base64
import hashlib
import hmac
import os
import zipfile
from xml.etree import ElementTree as ET

import pytest

from iafd import intunewin


@pytest.fixture
def setup_file(tmp_path):
    path = tmp_path / "setup.exe"
    # Larger than one chunk and not block-aligned, to exercise streaming and padding.
    path.write_bytes(b"MZ" + os.urandom(intunewin.CHUNK + 12345))
    return path


def test_encrypted_layout_and_keys(tmp_path, setup_file):
    package = intunewin.build_package(setup_file, tmp_path / "work")
    raw = package.encrypted_path.read_bytes()
    assert package.encrypted_size == len(raw)
    assert raw[:32] == package.mac
    assert raw[32:48] == package.iv
    assert (len(raw) - 48) % 16 == 0
    assert hmac.new(package.mac_key, raw[32:], hashlib.sha256).digest() == package.mac
    info = package.encryption_info()
    assert info["profileIdentifier"] == "ProfileVersion1"
    assert len(base64.b64decode(info["encryptionKey"])) == 32
    assert len(base64.b64decode(info["macKey"])) == 32


def test_round_trip_restores_the_setup_file(tmp_path, setup_file):
    package = intunewin.build_package(setup_file, tmp_path / "work")
    out = intunewin.write_intunewin(package, tmp_path / "app.intunewin")
    with zipfile.ZipFile(out) as archive:
        assert sorted(archive.namelist()) == [
            "IntuneWinPackage/Contents/IntunePackage.intunewin",
            "IntuneWinPackage/Metadata/Detection.xml",
        ]
        root = ET.fromstring(archive.read("IntuneWinPackage/Metadata/Detection.xml"))
    assert root.findtext("SetupFile") == "setup.exe"
    assert int(root.findtext("UnencryptedContentSize")) == package.unencrypted_size
    extracted = intunewin.extract_intunewin(out, tmp_path / "extracted")
    assert (extracted / "setup.exe").read_bytes() == setup_file.read_bytes()


def test_tampering_is_detected(tmp_path, setup_file):
    package = intunewin.build_package(setup_file, tmp_path / "work")
    data = bytearray(package.encrypted_path.read_bytes())
    data[100] ^= 0xFF
    package.encrypted_path.write_bytes(bytes(data))
    out = intunewin.write_intunewin(package, tmp_path / "app.intunewin")
    with pytest.raises(ValueError):
        intunewin.extract_intunewin(out, tmp_path / "extracted")


def test_detection_xml_carries_msi_information(tmp_path, setup_file):
    package = intunewin.build_package(setup_file, tmp_path / "work")
    xml = intunewin.detection_xml(
        package,
        {
            "product_code": "{A-B}",
            "version": "1.2",
            "package_type": "perMachine",
            "publisher": "A&B",
        },
    )
    root = ET.fromstring(xml)
    assert root.findtext("MsiInfo/MsiProductCode") == "{A-B}"
    assert root.findtext("MsiInfo/MsiPublisher") == "A&B"
    assert root.findtext("MsiInfo/MsiExecutionContext") == "System"
