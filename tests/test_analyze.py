import shutil

import pytest

from iafd import exe, msi
from iafd.analyze import analyze
from iafd.download import adopt_upload


def adopt(sample, name, tmp_path):
    upload = tmp_path / "upload.bin"
    shutil.copy(sample(name), upload)
    return adopt_upload(upload, name, tmp_path)


def test_decode_stream_name():
    assert msi.decode_stream_name("䡀㽿䅤䈯䅦㾲䜱") != ""
    assert msi.decode_stream_name("plain") == "plain"


def test_package_type_mapping():
    assert msi.package_type("1") == "perMachine"
    assert msi.package_type("2") == "dualPurpose"
    assert msi.package_type(None) == "perUser"


def test_architecture_from_template():
    assert msi.architecture_from_template("x64;1033") == "x64"
    assert msi.architecture_from_template("Intel;1033") == "x86"
    assert msi.architecture_from_template("Arm64;0") == "arm64"


def test_not_an_msi(tmp_path):
    path = tmp_path / "fake.msi"
    path.write_bytes(b"not an msi")
    with pytest.raises(msi.MsiError):
        msi.read_properties(path)


def test_garbage_exe_does_not_crash(tmp_path):
    path = tmp_path / "fake.exe"
    path.write_bytes(b"MZ" + b"\x00" * 64)
    assert exe.read_exe(path).version == ""


@pytest.mark.samples
def test_real_msi(sample, tmp_path):
    analysis = analyze(adopt(sample, "7z.msi", tmp_path))
    assert analysis.kind == "msi"
    assert analysis.product_name == "7-Zip 24.08 (x64 edition)"
    assert analysis.publisher == "Igor Pavlov"
    assert analysis.version == "24.08.00.0"
    assert analysis.architecture == "x64"
    assert analysis.msi["product_code"] == "{23170F69-40C1-2702-2408-000001000000}"
    assert analysis.msi["package_type"] == "dualPurpose"
    assert analysis.install_command == 'msiexec /i "7z.msi" /qn /norestart'
    assert analysis.uninstall_command.startswith('msiexec /x "{23170F69')


@pytest.mark.samples
def test_real_exe(sample, tmp_path):
    analysis = analyze(adopt(sample, "7z.exe", tmp_path))
    assert analysis.kind == "exe"
    assert analysis.product_name == "7-Zip"
    assert analysis.version == "24.08"
    assert analysis.architecture == "any"
    assert analysis.install_command == '"7z.exe"'
