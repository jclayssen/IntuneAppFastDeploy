"""Sign-in with Microsoft Entra ID (authorization code flow with PKCE).

Each administrator signs in with their own account. Tokens stay in the memory
of the container: nothing is written to disk and a restart signs everyone out.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

import msal

from .config import Settings

COOKIE = "iafd_session"
LOGIN_TTL = 600


class AuthError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class Session:
    id: str
    user_id: str
    user_name: str
    login: str
    home_account_id: str
    expires: float
    data: dict = field(default_factory=dict)


class Auth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._client: msal.ClientApplication | None = None
        self._flows: dict[str, tuple[float, dict]] = {}
        self._sessions: dict[str, Session] = {}

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.public_url}/auth/callback"

    def _app(self) -> msal.ClientApplication:
        with self._lock:
            if self._client is None:
                authority = f"{self.settings.authority_host}/{self.settings.tenant_id}"
                if self.settings.client_secret:
                    self._client = msal.ConfidentialClientApplication(
                        self.settings.client_id,
                        client_credential=self.settings.client_secret,
                        authority=authority,
                    )
                else:
                    self._client = msal.PublicClientApplication(
                        self.settings.client_id, authority=authority
                    )
            return self._client

    def begin_login(self) -> str:
        """Return the Microsoft sign-in URL to send the browser to."""
        if not self.settings.configured:
            raise AuthError("error.not_configured")
        with self._lock:
            now = time.time()
            self._flows = {k: v for k, v in self._flows.items() if v[0] > now}
            try:
                flow = self._app().initiate_auth_code_flow(
                    self.settings.scopes, redirect_uri=self.redirect_uri, prompt="select_account"
                )
            except Exception as exc:  # noqa: BLE001 - wrong tenant id, no network, ...
                raise AuthError("error.login_unreachable", type(exc).__name__) from exc
            if "auth_uri" not in flow:
                raise AuthError("error.login_failed", str(flow.get("error_description", "")))
            self._flows[flow["state"]] = (now + LOGIN_TTL, flow)
            return flow["auth_uri"]

    def finish_login(self, query: dict[str, str]) -> Session:
        with self._lock:
            entry = self._flows.pop(query.get("state", ""), None)
            if entry is None or entry[0] < time.time():
                raise AuthError("error.login_expired")
            try:
                result = self._app().acquire_token_by_auth_code_flow(entry[1], query)
            except Exception as exc:  # noqa: BLE001 - state mismatch, no network, ...
                raise AuthError("error.login_failed", type(exc).__name__) from exc
            if "access_token" not in result:
                raise AuthError("error.login_failed", str(result.get("error_description", "")))
            claims = result.get("id_token_claims") or {}
            login = str(claims.get("preferred_username", "")).lower()
            allowed = self.settings.allowed_users
            if allowed and login not in allowed:
                raise AuthError("error.user_not_allowed", login)
            home_account_id = ""
            for account in self._app().get_accounts():
                if account.get("local_account_id") == claims.get("oid"):
                    home_account_id = account.get("home_account_id", "")
            session = Session(
                id=secrets.token_urlsafe(32),
                user_id=str(claims.get("oid", "")),
                user_name=str(claims.get("name") or login),
                login=login,
                home_account_id=home_account_id,
                expires=time.time() + self.settings.session_hours * 3600,
            )
            self._sessions[session.id] = session
            return session

    def session(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.expires < time.time():
                self._sessions.pop(session_id, None)
                return None
            return session

    def logout(self, session_id: str | None) -> None:
        with self._lock:
            session = self._sessions.pop(session_id or "", None)
            if session and self._client is not None:
                for account in self._client.get_accounts():
                    if account.get("home_account_id") == session.home_account_id:
                        self._client.remove_account(account)

    def token(self, session: Session) -> str:
        """A valid Graph token for this session, refreshed when needed."""
        with self._lock:
            account = next(
                (
                    item
                    for item in self._app().get_accounts()
                    if item.get("home_account_id") == session.home_account_id
                ),
                None,
            )
            result = (
                self._app().acquire_token_silent(self.settings.scopes, account=account)
                if account
                else None
            )
            if not result or "access_token" not in result:
                self._sessions.pop(session.id, None)
                raise AuthError("error.session_expired")
            return result["access_token"]
