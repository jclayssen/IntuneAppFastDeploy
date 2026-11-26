import httpx
import pytest

from iafd.download import DownloadError, download, finalize_name, safe_file_name

EXE = b"MZ" + b"\x00" * 100


def transport(handler):
    return httpx.MockTransport(handler)


def test_safe_file_name_strips_paths_and_bad_characters():
    assert safe_file_name("..\\..\\evil/set:up?.exe") == "set_up_.exe"


def test_finalize_name_fixes_the_extension():
    assert finalize_name("download", "msi") == "download.msi"
    assert finalize_name("setup.bin", "exe") == "setup.exe"
    assert finalize_name("app-1.2.3", "exe") == "app-1.2.3.exe"
    assert finalize_name("Setup.EXE", "exe") == "Setup.EXE"


def test_download_uses_content_disposition_and_hashes(tmp_path):
    def handler(request):
        return httpx.Response(
            200, content=EXE, headers={"content-disposition": 'attachment; filename="My App.exe"'}
        )

    result = download("https://example.test/latest", tmp_path, 10_000, transport=transport(handler))
    assert result.file_name == "My App.exe"
    assert result.size == len(EXE)
    assert result.path.read_bytes() == EXE
    assert len(result.sha256) == 64


def test_download_follows_redirects_and_names_from_final_url(tmp_path):
    def handler(request):
        if request.url.path == "/latest":
            return httpx.Response(302, headers={"location": "https://cdn.test/files/app-2.0.exe"})
        return httpx.Response(200, content=EXE)

    result = download("https://example.test/latest", tmp_path, 10_000, transport=transport(handler))
    assert result.file_name == "app-2.0.exe"


@pytest.mark.parametrize(
    ("url", "body", "status", "limit", "code"),
    [
        ("ftp://example.test/a.exe", EXE, 200, 10_000, "error.url_scheme"),
        ("https://example.test/a.exe", b"<html>login</html>", 200, 10_000, "error.not_installer"),
        ("https://example.test/a.exe", EXE, 404, 10_000, "error.download_http"),
        ("https://example.test/a.exe", EXE, 200, 50, "error.too_large"),
    ],
)
def test_download_rejects(tmp_path, url, body, status, limit, code):
    def handler(request):
        return httpx.Response(status, content=body)

    with pytest.raises(DownloadError) as error:
        download(url, tmp_path, limit, transport=transport(handler))
    assert error.value.code == code
    assert not list(tmp_path.glob("*"))
