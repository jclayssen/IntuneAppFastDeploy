"""Read the metadata of an EXE installer and guess which installer made it."""

from __future__ import annotations

import mmap
from dataclasses import dataclass
from pathlib import Path

import pefile

_MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}


@dataclass(frozen=True)
class InstallerType:
    key: str
    label: str
    install_args: str
    # Uninstall cannot be known from the file: this is only a hint for the user.
    uninstall_hint: str


INSTALLER_TYPES: dict[str, InstallerType] = {
    "inno": InstallerType(
        "inno",
        "Inno Setup",
        "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-",
        r'"%ProgramFiles%\<App>\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
    ),
    "nsis": InstallerType("nsis", "NSIS", "/S", r'"%ProgramFiles%\<App>\uninstall.exe" /S'),
    "burn": InstallerType(
        "burn", "WiX Burn", "/quiet /norestart", "{setup} /uninstall /quiet /norestart"
    ),
    "installshield": InstallerType(
        "installshield",
        "InstallShield",
        '/s /v"/qn /norestart"',
        'msiexec /x "{ProductCode}" /qn /norestart',
    ),
    "advanced": InstallerType(
        "advanced",
        "Advanced Installer",
        "/exenoui /qn /norestart",
        'msiexec /x "{ProductCode}" /qn /norestart',
    ),
    "squirrel": InstallerType(
        "squirrel",
        "Squirrel",
        "--silent",
        r'"%LocalAppData%\<App>\Update.exe" --uninstall -s',
    ),
}

# Order matters: the first match wins.
_SIGNATURES: list[tuple[str, tuple[bytes, ...]]] = [
    ("inno", (b"Inno Setup Setup Data", b"JR.Inno.Setup")),
    ("nsis", (b"Nullsoft.NSIS", b"NullsoftInst")),
    ("squirrel", (b"SquirrelSetup", b"Squirrel.Windows")),
    ("advanced", (b"Advanced Installer",)),
    ("installshield", (b"InstallShield",)),
]


@dataclass
class ExeInfo:
    product_name: str = ""
    publisher: str = ""
    version: str = ""
    architecture: str = "x86"
    installer_type: str | None = None


def _clean_version(value: str) -> str:
    return value.replace(", ", ".").replace(",", ".").strip().split(" ", 1)[0]


def _detect_type(path: Path, pe: pefile.PE) -> str | None:
    if any(section.Name.rstrip(b"\x00") == b".wixburn" for section in pe.sections):
        return "burn"
    if path.stat().st_size == 0:
        return None
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for key, needles in _SIGNATURES:
            if any(data.find(needle) != -1 for needle in needles):
                return key
    return None


def read_exe(path: Path) -> ExeInfo:
    info = ExeInfo()
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except pefile.PEFormatError:
        return info
    try:
        info.architecture = _MACHINES.get(pe.FILE_HEADER.Machine, "x86")
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        strings: dict[str, str] = {}
        for file_info in getattr(pe, "FileInfo", []) or []:
            for entry in file_info:
                for table in getattr(entry, "StringTable", []) or []:
                    for key, value in table.entries.items():
                        strings[key.decode("utf-8", "replace")] = value.decode(
                            "utf-8", "replace"
                        ).strip()
        info.product_name = strings.get("ProductName") or strings.get("FileDescription", "")
        info.publisher = strings.get("CompanyName", "")
        info.version = _clean_version(
            strings.get("ProductVersion") or strings.get("FileVersion", "")
        )
        if not info.version and hasattr(pe, "VS_FIXEDFILEINFO"):
            fixed = pe.VS_FIXEDFILEINFO[0]
            info.version = (
                f"{fixed.FileVersionMS >> 16}.{fixed.FileVersionMS & 0xFFFF}"
                f".{fixed.FileVersionLS >> 16}.{fixed.FileVersionLS & 0xFFFF}"
            )
        info.installer_type = _detect_type(path, pe)
    finally:
        pe.close()
    return info
