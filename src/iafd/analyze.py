"""Turn a downloaded installer into the settings Intune needs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import exe, msi
from .download import DownloadResult


@dataclass
class Analysis:
    kind: str  # "msi" or "exe"
    file_name: str
    size: int
    sha256: str
    product_name: str = ""
    publisher: str = ""
    version: str = ""
    architecture: str = "x64"  # "x64", "x86", "arm64" or "any" (unknown: allow x86 and x64)
    install_command: str = ""
    uninstall_command: str = ""
    uninstall_hint: str = ""
    installer_type: str | None = None
    installer_label: str | None = None
    msi: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def analyze(result: DownloadResult) -> Analysis:
    path: Path = result.path
    kind = "msi" if result.file_name.lower().endswith(".msi") else "exe"
    analysis = Analysis(
        kind=kind, file_name=result.file_name, size=result.size, sha256=result.sha256
    )
    if kind == "msi":
        properties = msi.read_properties(path)
        product_code = properties.get("ProductCode", "")
        analysis.product_name = properties.get("ProductName", "")
        analysis.publisher = properties.get("Manufacturer", "")
        analysis.version = properties.get("ProductVersion", "")
        analysis.architecture = msi.architecture_from_template(properties.get("_Template", ""))
        analysis.install_command = f'msiexec /i "{result.file_name}" /qn /norestart'
        analysis.uninstall_command = f'msiexec /x "{product_code}" /qn /norestart'
        analysis.installer_label = "Windows Installer (MSI)"
        analysis.msi = {
            "product_code": product_code,
            "upgrade_code": properties.get("UpgradeCode", ""),
            "package_type": msi.package_type(properties.get("ALLUSERS")),
        }
    else:
        info = exe.read_exe(path)
        analysis.product_name = info.product_name
        analysis.publisher = info.publisher
        analysis.version = info.version
        # A 32-bit setup stub often installs a 64-bit program, so "x86" proves nothing.
        analysis.architecture = info.architecture if info.architecture != "x86" else "any"
        analysis.installer_type = info.installer_type
        kind_info = exe.INSTALLER_TYPES.get(info.installer_type or "")
        if kind_info:
            analysis.installer_label = kind_info.label
            analysis.install_command = f'"{result.file_name}" {kind_info.install_args}'
            analysis.uninstall_hint = kind_info.uninstall_hint.replace(
                "{setup}", f'"{result.file_name}"'
            )
        else:
            analysis.install_command = f'"{result.file_name}"'
    return analysis
