import pytest

from iafd.auth import Auth, AuthError
from iafd.config import Settings


class FakeMsal:
    """Stands in for the MSAL client: no network, predictable answers."""

    def __init__(self):
        self.accounts = []
        self.silent = {"access_token": "token-1"}

    def initiate_auth_code_flow(self, scopes, redirect_uri=None, prompt=None):
        self.scopes, self.redirect_uri = scopes, redirect_uri
        return {"state": "s1", "auth_uri": "https://login.test/authorize?state=s1"}

    def acquire_token_by_auth_code_flow(self, flow, query):
        if query.get("code") != "good":
            return {"error": "invalid_grant", "error_description": "bad code"}
        self.accounts = [{"local_account_id": "oid-1", "home_account_id": "home-1"}]
        return {
            "access_token": "token-0",
            "id_token_claims": {"oid": "oid-1", "name": "Ada", "preferred_username": "Ada@X.test"},
        }

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent(self, scopes, account=None):
        return self.silent

    def remove_account(self, account):
        self.accounts.remove(account)


def make(**overrides):
    settings = Settings(tenant_id="tenant", client_id="client", **overrides)
    auth = Auth(settings)
    auth._client = FakeMsal()
    return auth


def test_sign_in_creates_a_session_and_tokens_are_refreshed_silently():
    auth = make(public_url="https://deploy.example.test", enable_group_picker=True)
    assert auth.begin_login() == "https://login.test/authorize?state=s1"
    assert auth._client.redirect_uri == "https://deploy.example.test/auth/callback"
    assert auth._client.scopes == [
        "https://graph.microsoft.com/DeviceManagementApps.ReadWrite.All",
        "https://graph.microsoft.com/GroupMember.Read.All",
    ]
    session = auth.finish_login({"state": "s1", "code": "good"})
    assert session.user_name == "Ada" and session.login == "ada@x.test"
    assert auth.session(session.id) is session
    assert auth.token(session) == "token-1"
    auth.logout(session.id)
    assert auth.session(session.id) is None and auth._client.accounts == []


def test_a_login_answer_cannot_be_replayed_or_forged():
    auth = make()
    auth.begin_login()
    with pytest.raises(AuthError, match="error.login_expired"):
        auth.finish_login({"state": "other", "code": "good"})
    auth.finish_login({"state": "s1", "code": "good"})
    with pytest.raises(AuthError, match="error.login_expired"):
        auth.finish_login({"state": "s1", "code": "good"})


def test_refused_code_and_user_restriction():
    auth = make()
    auth.begin_login()
    with pytest.raises(AuthError, match="error.login_failed"):
        auth.finish_login({"state": "s1", "code": "bad"})
    restricted = make(allowed_users=["boss@x.test"])
    restricted.begin_login()
    with pytest.raises(AuthError, match="error.user_not_allowed"):
        restricted.finish_login({"state": "s1", "code": "good"})


def test_lost_refresh_token_ends_the_session():
    auth = make()
    auth.begin_login()
    session = auth.finish_login({"state": "s1", "code": "good"})
    auth._client.silent = None
    with pytest.raises(AuthError, match="error.session_expired"):
        auth.token(session)
    assert auth.session(session.id) is None


def test_missing_configuration_is_reported():
    with pytest.raises(AuthError, match="error.not_configured"):
        Auth(Settings()).begin_login()
