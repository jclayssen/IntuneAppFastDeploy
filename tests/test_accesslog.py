"""The access log, read from a real Uvicorn server started the way ``iafd.web`` starts it."""

import io
import logging
import threading
import time

import httpx
import pytest
import uvicorn

from iafd.accesslog import PathOnlyAccessLog, log_config
from iafd.config import Settings
from iafd.web import create_app

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def served(tmp_path):
    """A running server: its address and everything its access log writes."""
    settings = Settings(tenant_id="t", client_id="c", config_dir=tmp_path, work_dir=tmp_path / "w")
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=0,
        log_level="info",
        log_config=log_config(),
        use_colors=False,
    )
    server = uvicorn.Server(config)
    output = io.StringIO()
    # The handler Uvicorn itself writes with, pointed at a buffer instead of stdout.
    logging.getLogger("uvicorn.access").handlers[0].setStream(output)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", output
    finally:
        server.should_exit = True
        thread.join(10)
        for name in UVICORN_LOGGERS:
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
            for log_filter in logger.filters[:]:
                logger.removeFilter(log_filter)
        assert not thread.is_alive(), "the server did not stop"


def test_sign_in_return_leaves_no_query_string_in_the_access_log(served):
    url, output = served
    query = "code=SECRET-CODE&client_info=SECRET-INFO&state=SECRET-STATE&session_state=SECRET-SID"

    response = httpx.get(f"{url}/auth/callback?{query}", follow_redirects=False)

    assert response.status_code == 302
    logged = output.getvalue()
    assert '"GET /auth/callback HTTP/1.1" 302' in logged
    assert "SECRET" not in logged
    assert "?" not in logged


def test_other_routes_are_logged_without_their_query_string(served):
    url, output = served

    httpx.get(f"{url}/healthz?token=SECRET")
    httpx.get(f"{url}/healthz")

    logged = output.getvalue()
    assert logged.count('"GET /healthz HTTP/1.1" 200') == 2
    assert "SECRET" not in logged


def record(*args):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', args, None
    )


def test_filter_keeps_method_path_and_status():
    entry = record("10.0.0.1:1234", "GET", "/api/apps?search=SECRET", "1.1", 200)

    assert PathOnlyAccessLog().filter(entry) is True

    assert entry.getMessage() == '10.0.0.1:1234 - "GET /api/apps HTTP/1.1" 200'


def test_filter_drops_a_record_it_cannot_read():
    # Another shape than the one Uvicorn uses today: better no line than a leaking one.
    entry = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, "GET /auth/callback?code=SECRET", (), None
    )

    assert PathOnlyAccessLog().filter(entry) is False
