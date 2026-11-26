"""Read the metadata of an MSI file without Windows.

An MSI is an OLE compound file. Table streams have mangled names, strings live
in a shared pool, and tables are stored column by column. Only the ``Property``
table and the summary information are needed here.
"""

from __future__ import annotations

import struct
from pathlib import Path

import olefile

_B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"
_CODEPAGES = {0: "cp1252", 65001: "utf-8", 932: "cp932", 936: "gbk", 949: "cp949", 950: "big5"}


class MsiError(Exception):
    pass


def decode_stream_name(name: str) -> str:
    out: list[str] = []
    for char in name:
        code = ord(char)
        if 0x3800 <= code < 0x4800:
            code -= 0x3800
            out.append(_B64[code & 0x3F])
            out.append(_B64[(code >> 6) & 0x3F])
        elif 0x4800 <= code < 0x4840:
            out.append(_B64[code - 0x4800])
        elif code == 0x4840:
            out.append("!")
        else:
            out.append(char)
    return "".join(out)


def _read_string_pool(pool: bytes, data: bytes) -> tuple[list[str], int]:
    """Return the strings (index 0 is the empty string) and the reference size."""
    if len(pool) < 4:
        raise MsiError("string pool missing")
    (header,) = struct.unpack_from("<I", pool, 0)
    ref_size = 3 if header & 0x80000000 else 2
    codepage = header & 0x7FFFFFFF
    encoding = _CODEPAGES.get(codepage, f"cp{codepage}")
    strings = [""]
    offset = 0
    position = 4
    while position + 4 <= len(pool):
        length, refs = struct.unpack_from("<HH", pool, position)
        position += 4
        if length == 0 and refs > 0 and position + 4 <= len(pool):
            # Strings over 64 KB: the high word of the length sits in this
            # entry's reference count and the low word in the next entry.
            low, _ = struct.unpack_from("<HH", pool, position)
            position += 4
            length = (refs << 16) | low
        raw = data[offset : offset + length]
        offset += length
        try:
            strings.append(raw.decode(encoding))
        except (LookupError, UnicodeDecodeError):
            strings.append(raw.decode("latin-1"))
    return strings, ref_size


def _read_ref(buffer: bytes, position: int, ref_size: int) -> int:
    if ref_size == 2:
        return struct.unpack_from("<H", buffer, position)[0]
    return buffer[position] | (buffer[position + 1] << 8) | (buffer[position + 2] << 16)


def read_properties(path: Path) -> dict[str, str]:
    """Return the ``Property`` table of an MSI as a dictionary."""
    if not olefile.isOleFile(str(path)):
        raise MsiError("not an MSI file")
    with olefile.OleFileIO(str(path)) as ole:
        streams = {
            decode_stream_name(entry[0]): entry for entry in ole.listdir() if len(entry) == 1
        }
        try:
            pool = ole.openstream(streams["!_StringPool"]).read()
            data = ole.openstream(streams["!_StringData"]).read()
            table = ole.openstream(streams["!Property"]).read()
        except KeyError as exc:
            raise MsiError(f"missing MSI stream {exc}") from exc
        strings, ref_size = _read_string_pool(pool, data)
        rows = len(table) // (2 * ref_size)
        properties: dict[str, str] = {}
        for row in range(rows):
            key = _read_ref(table, row * ref_size, ref_size)
            value = _read_ref(table, (rows + row) * ref_size, ref_size)
            if 0 < key < len(strings) and value < len(strings):
                properties[strings[key]] = strings[value]
        template = ""
        try:
            summary = ole.getproperties("\x05SummaryInformation")
            raw = summary.get(7, "")
            template = raw.decode("latin-1") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001 - summary info is optional
            template = ""
        properties["_Template"] = template
    return properties


def architecture_from_template(template: str) -> str:
    platform = template.split(";", 1)[0].strip().lower()
    if platform in ("x64", "amd64", "intel64"):
        return "x64"
    if platform == "arm64":
        return "arm64"
    return "x86"


def package_type(all_users: str | None) -> str:
    """Map the ALLUSERS property to the Intune package type."""
    if all_users == "1":
        return "perMachine"
    if all_users == "2":
        return "dualPurpose"
    return "perUser"
