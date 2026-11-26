"""Download an installer from a link, with a size limit and a safe file name."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; IntuneAppFastDeploy)"
CHUNK = 1024 * 1024
MSI_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
EXE_MAGIC = b"MZ"


class DownloadError(Exception):
    """Raised with a translation key (``code``) the UI can display."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class DownloadResult:
    path: Path
    file_name: str
    size: int
    sha256: str
    source: str


def safe_file_name(name: str) -> str:
    """Keep only the last path component and characters Windows accepts."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150]


def _name_from_headers(response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*[\w-]*''([^;]+)", disposition, re.I)
    if match:
        return unquote(match.group(1).strip())
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
    if match:
        return match.group(1).strip()
    return unquote(urlsplit(str(response.url)).path.rsplit("/", 1)[-1])


def sniff_kind(head: bytes) -> str | None:
    if head.startswith(MSI_MAGIC):
        return "msi"
    if head.startswith(EXE_MAGIC):
        return "exe"
    return None


def finalize_name(name: str, kind: str) -> str:
    """Make sure the file name carries the extension matching its real content."""
    name = safe_file_name(name) or "setup"
    if not name.lower().endswith(f".{kind}"):
        stem = name.rsplit(".", 1)[0] if re.search(r"\.[A-Za-z]{2,8}$", name) else name
        name = f"{stem}.{kind}"
    return name


def download(
    url: str,
    dest_dir: Path,
    max_bytes: int,
    progress: Callable[[int, int | None], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> DownloadResult:
    """Stream ``url`` into ``dest_dir``. Only MSI and EXE files are accepted."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise DownloadError("error.url_scheme")
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "download.part"
    digest = hashlib.sha256()
    size = 0
    kind: str | None = None
    try:
        with (
            httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(30, read=120),
                headers={"User-Agent": USER_AGENT},
                transport=transport,
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code >= 400:
                raise DownloadError("error.download_http", str(response.status_code))
            total = int(response.headers.get("content-length") or 0) or None
            if total and total > max_bytes:
                raise DownloadError("error.too_large")
            name = _name_from_headers(response)
            with tmp.open("wb") as out:
                for chunk in response.iter_bytes(CHUNK):
                    if size == 0:
                        kind = sniff_kind(chunk[:8])
                        if kind is None:
                            raise DownloadError("error.not_installer")
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("error.too_large")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(size, total)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise DownloadError("error.download_failed", type(exc).__name__) from exc
    except DownloadError:
        tmp.unlink(missing_ok=True)
        raise
    if kind is None:
        tmp.unlink(missing_ok=True)
        raise DownloadError("error.not_installer")
    file_name = finalize_name(name, kind)
    path = dest_dir / file_name
    tmp.replace(path)
    return DownloadResult(path, file_name, size, digest.hexdigest(), url)


def adopt_upload(src: Path, original_name: str, dest_dir: Path) -> DownloadResult:
    """Register a file the user uploaded directly instead of giving a link."""
    digest = hashlib.sha256()
    size = 0
    with src.open("rb") as handle:
        head = handle.read(8)
        kind = sniff_kind(head)
        if kind is None:
            raise DownloadError("error.not_installer")
        handle.seek(0)
        while chunk := handle.read(CHUNK):
            size += len(chunk)
            digest.update(chunk)
    file_name = finalize_name(original_name, kind)
    path = dest_dir / file_name
    src.replace(path)
    return DownloadResult(path, file_name, size, digest.hexdigest(), "upload")
