"""Create ``.intunewin`` packages on any platform.

Format (same as Microsoft's Win32 Content Prep Tool):

* the setup file is zipped;
* that zip is encrypted with AES-256-CBC (PKCS7 padding) into
  ``IntunePackage.intunewin`` laid out as ``HMAC-SHA256 (32) | IV (16) | data``,
  the HMAC covering ``IV | data``;
* the outer ``.intunewin`` is a zip holding that encrypted file and a
  ``Detection.xml`` with the keys.

Intune itself only receives the encrypted file plus the keys, so the outer zip
is only built when the user wants to download the package.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CHUNK = 1024 * 1024
ENCRYPTED_NAME = "IntunePackage.intunewin"
TOOL_VERSION = "1.8.4.0"


@dataclass
class Package:
    encrypted_path: Path
    setup_file: str
    unencrypted_size: int
    encrypted_size: int
    encryption_key: bytes
    mac_key: bytes
    iv: bytes
    mac: bytes
    file_digest: bytes

    def encryption_info(self) -> dict[str, str]:
        """The ``fileEncryptionInfo`` body Microsoft Graph expects on commit."""
        return {
            "encryptionKey": _b64(self.encryption_key),
            "macKey": _b64(self.mac_key),
            "initializationVector": _b64(self.iv),
            "mac": _b64(self.mac),
            "profileIdentifier": "ProfileVersion1",
            "fileDigest": _b64(self.file_digest),
            "fileDigestAlgorithm": "SHA256",
        }


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def build_package(setup_path: Path, work_dir: Path) -> Package:
    """Zip then encrypt ``setup_path``. Returns the keys Intune needs."""
    work_dir.mkdir(parents=True, exist_ok=True)
    inner = work_dir / "content.zip"
    encrypted = work_dir / ENCRYPTED_NAME
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(setup_path, setup_path.name)

    encryption_key = os.urandom(32)
    mac_key = os.urandom(32)
    iv = os.urandom(16)
    digest = hashlib.sha256()
    signer = hmac.new(mac_key, digestmod=hashlib.sha256)
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    padder = padding.PKCS7(128).padder()
    signer.update(iv)
    with inner.open("rb") as source, encrypted.open("wb") as target:
        target.write(b"\x00" * 32)
        target.write(iv)
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            block = encryptor.update(padder.update(chunk))
            signer.update(block)
            target.write(block)
        block = encryptor.update(padder.finalize()) + encryptor.finalize()
        signer.update(block)
        target.write(block)
        mac = signer.digest()
        target.seek(0)
        target.write(mac)
    unencrypted_size = inner.stat().st_size
    inner.unlink()
    return Package(
        encrypted_path=encrypted,
        setup_file=setup_path.name,
        unencrypted_size=unencrypted_size,
        encrypted_size=encrypted.stat().st_size,
        encryption_key=encryption_key,
        mac_key=mac_key,
        iv=iv,
        mac=mac,
        file_digest=digest.digest(),
    )


def detection_xml(package: Package, msi: dict[str, str] | None = None) -> str:
    info = package.encryption_info()
    lines = [
        '<ApplicationInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'ToolVersion="{TOOL_VERSION}">',
        f"  <Name>{escape(package.setup_file)}</Name>",
        f"  <UnencryptedContentSize>{package.unencrypted_size}</UnencryptedContentSize>",
        f"  <FileName>{ENCRYPTED_NAME}</FileName>",
        f"  <SetupFile>{escape(package.setup_file)}</SetupFile>",
        "  <EncryptionInfo>",
        f"    <EncryptionKey>{info['encryptionKey']}</EncryptionKey>",
        f"    <MacKey>{info['macKey']}</MacKey>",
        f"    <InitializationVector>{info['initializationVector']}</InitializationVector>",
        f"    <Mac>{info['mac']}</Mac>",
        "    <ProfileIdentifier>ProfileVersion1</ProfileIdentifier>",
        f"    <FileDigest>{info['fileDigest']}</FileDigest>",
        "    <FileDigestAlgorithm>SHA256</FileDigestAlgorithm>",
        "  </EncryptionInfo>",
    ]
    if msi and msi.get("product_code"):
        per_machine = msi.get("package_type") != "perUser"
        lines += [
            "  <MsiInfo>",
            f"    <MsiProductCode>{escape(msi['product_code'])}</MsiProductCode>",
            f"    <MsiProductVersion>{escape(msi.get('version', ''))}</MsiProductVersion>",
            f"    <MsiUpgradeCode>{escape(msi.get('upgrade_code', ''))}</MsiUpgradeCode>",
            f"    <MsiExecutionContext>{'System' if per_machine else 'User'}</MsiExecutionContext>",
            "    <MsiRequiresLogon>false</MsiRequiresLogon>",
            "    <MsiRequiresReboot>false</MsiRequiresReboot>",
            f"    <MsiIsMachineInstall>{str(per_machine).lower()}</MsiIsMachineInstall>",
            f"    <MsiIsUserInstall>{str(not per_machine).lower()}</MsiIsUserInstall>",
            "    <MsiIncludesServices>false</MsiIncludesServices>",
            "    <MsiIncludesODBCDataSource>false</MsiIncludesODBCDataSource>",
            "    <MsiContainsSystemRegistryKeys>false</MsiContainsSystemRegistryKeys>",
            "    <MsiContainsSystemFolders>false</MsiContainsSystemFolders>",
            f"    <MsiPublisher>{escape(msi.get('publisher', ''))}</MsiPublisher>",
            "  </MsiInfo>",
        ]
    lines.append("</ApplicationInfo>")
    return "\n".join(lines) + "\n"


def write_intunewin(package: Package, out_path: Path, msi: dict[str, str] | None = None) -> Path:
    """Write the downloadable ``.intunewin`` (for a manual upload in the portal)."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as archive:
        archive.write(package.encrypted_path, f"IntuneWinPackage/Contents/{ENCRYPTED_NAME}")
        archive.writestr("IntuneWinPackage/Metadata/Detection.xml", detection_xml(package, msi))
    return out_path


def extract_intunewin(intunewin_path: Path, out_dir: Path) -> Path:
    """Reverse of :func:`write_intunewin`: verify, decrypt and unzip a package.

    Used by the tests to prove a package round-trips, and handy for debugging.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(intunewin_path) as outer:
        xml = outer.read("IntuneWinPackage/Metadata/Detection.xml")
        root = ET.fromstring(xml)  # noqa: S314 - file produced by this tool
        key = base64.b64decode(root.findtext("EncryptionInfo/EncryptionKey") or "")
        mac_key = base64.b64decode(root.findtext("EncryptionInfo/MacKey") or "")
        expected_mac = base64.b64decode(root.findtext("EncryptionInfo/Mac") or "")
        expected_digest = base64.b64decode(root.findtext("EncryptionInfo/FileDigest") or "")
        inner_zip = out_dir / "content.zip"
        with outer.open(f"IntuneWinPackage/Contents/{ENCRYPTED_NAME}") as source:
            stored_mac = source.read(32)
            iv = source.read(16)
            signer = hmac.new(mac_key, iv, hashlib.sha256)
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            unpadder = padding.PKCS7(128).unpadder()
            digest = hashlib.sha256()
            with inner_zip.open("wb") as target:
                while chunk := source.read(CHUNK):
                    signer.update(chunk)
                    plain = unpadder.update(decryptor.update(chunk))
                    digest.update(plain)
                    target.write(plain)
                plain = unpadder.update(decryptor.finalize()) + unpadder.finalize()
                digest.update(plain)
                target.write(plain)
    if not hmac.compare_digest(signer.digest(), stored_mac) or stored_mac != expected_mac:
        raise ValueError("HMAC mismatch")
    if digest.digest() != expected_digest:
        raise ValueError("digest mismatch")
    with zipfile.ZipFile(inner_zip) as inner:
        inner.extractall(out_dir)  # noqa: S202 - file produced by this tool
    inner_zip.unlink()
    return out_dir
