"""The search of groups by name: what the server tells the interface, and the setup guide.

The interface explains how to enable the search. That explanation, both READMEs and the
code must name the same setting and the same permission, in an order that keeps sign-in
working: consent first, setting second.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iafd.config import Settings

from .demo import build

ROOT = Path(__file__).parent.parent
LOCALES = {
    path.stem: json.loads(path.read_text("utf-8"))
    for path in (ROOT / "src" / "iafd" / "locales").glob("*.json")
}
SETTING = "ENABLE_GROUP_PICKER"
# The steps of the guide shown in the interface, in the order they are displayed.
STEPS = [f"groups.setup_step{number}" for number in range(1, 6)]


def permission() -> str:
    """The delegated permission the option adds, read from the code."""
    added = set(Settings(enable_group_picker=True).scopes) - set(Settings().scopes)
    assert len(added) == 1
    return added.pop().rsplit("/", 1)[1]


def signed_in(**options):
    client = TestClient(build(**options))
    client.headers["X-IAFD"] = "1"
    return client


def test_config_says_the_search_is_off_by_default():
    with signed_in() as client:
        assert client.get("/api/config").json()["group_picker"] is False
        response = client.get("/api/groups", params={"q": "Pilot"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "error.group_picker_disabled"


def test_config_says_the_search_is_on_and_groups_are_found_by_the_start_of_their_name():
    with signed_in(group_picker=True) as client:
        assert client.get("/api/config").json()["group_picker"] is True
        found = client.get("/api/groups", params={"q": "pilot"}).json()["groups"]
        assert found, "the demo directory has a pilot group"
        assert all(group["name"].lower().startswith("pilot") for group in found)
        assert set(found[0]) == {"id", "name"}
        everything = client.get("/api/groups").json()["groups"]
        assert len(everything) > len(found)
        assert client.get("/api/groups", params={"q": "zzz"}).json()["groups"] == []


def test_a_name_with_an_apostrophe_is_searched_as_typed():
    app = build(group_picker=True)
    app.state.intune.directory["55555555-5555-5555-5555-000000000099"] = "L'atelier"
    with TestClient(app) as client:
        client.headers["X-IAFD"] = "1"
        found = client.get("/api/groups", params={"q": "l'at"}).json()["groups"]
        assert [group["name"] for group in found] == ["L'atelier"]
        # A quote cannot end the filter and add a condition of its own.
        assert client.get("/api/groups", params={"q": "x') or ('"}).json()["groups"] == []


def test_the_option_adds_exactly_one_read_only_permission():
    assert permission() == "GroupMember.Read.All"


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_the_guide_in_the_interface_names_the_permission_and_the_setting(code):
    texts = LOCALES[code]
    steps = [texts[key] for key in STEPS]
    with_permission = [index for index, text in enumerate(steps) if permission() in text]
    with_setting = [index for index, text in enumerate(steps) if SETTING in text]
    assert with_permission, f"{code}: no step names {permission()}"
    assert with_setting, f"{code}: no step names {SETTING}"
    # A permission that has not been consented breaks sign-in: consent comes first.
    assert max(with_permission) < min(with_setting)
    assert "docker compose up -d" in steps[-1]
    assert "docker-compose.yml" in steps[with_setting[0]]
    # The warning about the order names the setting to turn back off.
    assert '"false"' in texts["groups.setup_order"]


@pytest.mark.parametrize("name", ["README.md", "README.fr.md"])
def test_the_readme_gives_the_same_setup(name):
    text = (ROOT / name).read_text("utf-8")
    assert permission() in text
    assert f'{SETTING}: "true"' in text
    # Same order as in the interface: the permission is explained before the setting.
    assert text.index(permission()) < text.index(f'{SETTING}: "true"')


def test_the_compose_file_ships_with_the_search_off():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert f'{SETTING}: "false"' in compose
    assert permission() in compose
