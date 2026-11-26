"""Software the tool refuses to remove from the devices.

Removing one of these breaks device management, security or Windows itself.
The list is enforced on the server: the interface cannot bypass it.

Add your own entries in ``config/protected.txt``, one per line: an exact name,
or ``re:`` followed by a regular expression. Lines starting with ``#`` are
comments. The built-in entries cannot be switched off.
"""

from __future__ import annotations

import re
from pathlib import Path

BUILT_IN = [
    # Intune and device management
    r"^microsoft intune management extension$",
    r"company portal|portail d'entreprise",
    r"^microsoft\.companyportal$",
    r"^microsoft update health tools$",
    r"^configuration manager client$",
    # Security
    r"defender",
    r"^windows security$",
    r"^microsoft\.sechealthui$",
    r"\blaps\b",
    # Windows itself and its runtimes
    r"^microsoft edge( update)?$",
    r"^microsoft edge webview2",
    r"^microsoft visual c\+\+ .*(redistributable|runtime)",
    r"^microsoft \.net\b",
    r"^microsoft windows desktop runtime",
    r"^microsoft asp\.net core",
    r"^windows (software development kit|driver|pc health check)",
    r"^microsoft\.(windowsstore|storepurchaseapp|desktopappinstaller|vclibs|net\.native|ui\.xaml)",
    r"^microsoft\.(windowsappruntime|aad\.brokerplugin|accountscontrol|lockapp|windows\.)",
    # Microsoft 365
    r"^microsoft 365 apps",
    r"^microsoft office .*click-to-run",
    r"^office 16 click-to-run",
    r"^microsoft onedrive$",
    r"^microsoft teams meeting add-in",
    # Hardware drivers
    r"(intel|nvidia|amd|realtek|qualcomm|synaptics|mediatek|broadcom)\b.*"
    r"(driver|pilote|chipset|graphics|audio|bluetooth|wireless|wi-fi|management engine|serial io)",
]

_BUILT_IN = [re.compile(pattern, re.IGNORECASE) for pattern in BUILT_IN]


def load_extra(config_dir: Path) -> list[re.Pattern]:
    """Entries added by the administrator in ``config/protected.txt``."""
    patterns: list[re.Pattern] = []
    try:
        lines = (config_dir / "protected.txt").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return patterns
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if line.lower().startswith("re:"):
                patterns.append(re.compile(line[3:].strip(), re.IGNORECASE))
            else:
                patterns.append(re.compile(f"^{re.escape(line)}$", re.IGNORECASE))
        except re.error:
            continue  # a broken line protects nothing, it must not break the tool
    return patterns


def is_protected(name: str, extra: list[re.Pattern] | None = None) -> bool:
    name = name.strip()
    return any(pattern.search(name) for pattern in [*_BUILT_IN, *(extra or [])])


def blocked(names: list[str], extra: list[re.Pattern] | None = None) -> list[str]:
    """The names of the list that must not be removed."""
    return [name for name in names if is_protected(name, extra)]
