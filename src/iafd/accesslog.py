"""The access log of the server: method, path and status, never the query string.

The return from the Microsoft sign-in reaches ``/auth/callback`` with the
authorization code, the account identifiers and the session state in its query
string. Uvicorn logs the full request target by default, which would copy all
of that into ``docker compose logs``.

Only HTTP requests are covered: Uvicorn logs an accepted WebSocket through
another logger, and this application has no WebSocket route.
"""

from __future__ import annotations

import copy
import logging

from uvicorn.config import LOGGING_CONFIG

# Uvicorn logs ``'%s - "%s %s HTTP/%s" %d'`` with these five values.
ACCESS_ARGS = 5
TARGET = 2


class PathOnlyAccessLog(logging.Filter):
    """Cut the request target of an access record down to its path."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != ACCESS_ARGS:
            # Not the shape Uvicorn uses today: better no line than a leaking one.
            return False
        target = str(args[TARGET]).partition("?")[0]
        record.args = (*args[:TARGET], target, *args[TARGET + 1 :])
        return True


def log_config() -> dict:
    """Uvicorn's logging configuration, with the filter on the access logger."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        **config.get("filters", {}),
        "path_only": {"()": f"{__name__}.{PathOnlyAccessLog.__name__}"},
    }
    config["loggers"]["uvicorn.access"]["filters"] = ["path_only"]
    return config
