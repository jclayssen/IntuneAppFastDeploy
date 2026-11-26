"""Shared fixtures. Real installers are downloaded once, on demand."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

SAMPLES = {
    "7z.msi": "https://www.7-zip.org/a/7z2408-x64.msi",
    "7z.exe": "https://www.7-zip.org/a/7z2408-x64.exe",
}
CACHE = Path(__file__).parent / ".samples"


@pytest.fixture(scope="session")
def sample():
    def get(name: str) -> Path:
        path = CACHE / name
        if not path.exists():
            CACHE.mkdir(exist_ok=True)
            try:
                response = httpx.get(SAMPLES[name], follow_redirects=True, timeout=60)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                pytest.skip(f"sample {name} unavailable: {exc}")
            path.write_bytes(response.content)
        return path

    return get
