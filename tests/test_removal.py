import base64
import json
import re

import pytest

from iafd import removal


def embedded(script: str) -> dict:
    match = re.search(r"\$ConfigBase64 = '([A-Za-z0-9+/=]+)'", script)
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def test_render_embeds_settings_as_data_and_sets_the_mode():
    script = removal.render_script({"names": ["7-Zip 19.00 (x64 edition)"]}, "Uninstall")
    assert "[string]$Mode = 'Uninstall'" in script
    assert "@@" not in script
    config = embedded(script)
    assert config["names"] == ["7-Zip 19.00 (x64 edition)"]
    assert config["kind"] == "machine" and config["versions"] == {"mode": "any"}
    assert config["slug"] == "7-zip-19-00-x64-edition"


def test_hostile_text_cannot_become_code():
    hostile = "x'; Remove-Item C:\\ -Recurse; '"
    script = removal.render_script(
        {"names": [hostile], "command": '{UninstallString} /S & echo "$(whoami)"'}, "Detect"
    )
    # Nothing typed by a person appears in the script: only its base64 form does.
    assert "Remove-Item C:" not in script and "whoami" not in script
    assert embedded(script)["names"] == [hostile]
    # The template itself is unchanged apart from the two placeholders.
    template = removal.TEMPLATE.read_text(encoding="utf-8")
    assert len(script.splitlines()) == len(template.splitlines())


def test_names_are_trimmed_and_deduplicated_ignoring_case():
    config = removal.clean_config({"names": [" VLC ", "vlc", "", "Zoom"]})
    assert config["names"] == ["VLC", "Zoom"]


def test_accents_survive():
    script = removal.render_script({"names": ["Éditeur de schémas"]}, "Detect")
    assert embedded(script)["names"] == ["Éditeur de schémas"]


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"names": []}, "error.removal_no_name"),
        ({"names": ["  "]}, "error.removal_no_name"),
        ({"names": ["a"] * 1 + [str(i) for i in range(60)]}, "error.removal_bad_name"),
        ({"names": ["a"], "kind": "everything"}, "error.removal_bad_kind"),
        (
            {"names": ["a"], "versions": {"mode": "list", "values": []}},
            "error.removal_bad_versions",
        ),
        (
            {"names": ["a"], "versions": {"mode": "below", "value": "latest"}},
            "error.removal_bad_versions",
        ),
        ({"names": ["a"], "versions": {"mode": "older"}}, "error.removal_bad_versions"),
        ({"names": ["a"], "command": "a\nb"}, "error.removal_bad_command"),
    ],
)
def test_invalid_settings_are_refused(raw, error):
    with pytest.raises(removal.RemovalError, match=error):
        removal.clean_config(raw)


def test_version_rules_and_limits():
    below = removal.clean_config({"names": ["a"], "versions": {"mode": "below", "value": "24.8"}})
    assert below["versions"] == {"mode": "below", "value": "24.8"}
    listed = removal.clean_config(
        {"names": ["a"], "versions": {"mode": "list", "values": ["1.0", " "]}}
    )
    assert listed["versions"] == {"mode": "list", "values": ["1.0"]}
    assert removal.clean_config({"names": ["a"], "timeout_seconds": 1})["timeout_seconds"] == 5
    assert (
        removal.clean_config({"names": ["a"], "timeout_seconds": 99999})["timeout_seconds"] == 7200
    )
    assert removal.clean_config({"names": ["a"], "timeout_seconds": "x"})["timeout_seconds"] == 1800
    # A Store app is removed by Windows: a command would be meaningless.
    assert (
        removal.clean_config({"names": ["a"], "kind": "store", "command": "x /S"})["command"] == ""
    )


def test_unknown_mode_is_refused():
    with pytest.raises(removal.RemovalError, match="error.removal_bad_mode"):
        removal.render_script({"names": ["a"]}, "Library")


def test_command_line_entry_point_writes_a_script_with_a_byte_order_mark(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"names": ["Été"]}), encoding="utf-8")
    out = tmp_path / "out.ps1"
    assert removal.main(["Detect", str(config), str(out)]) == 0
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert removal.main([]) == 2


def test_the_template_avoids_syntax_newer_than_windows_powershell_5():
    template = removal.TEMPLATE.read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in template.splitlines())
    for newer in ("??", "?.", "-AsHashtable", " ? ", "&&", "||"):
        assert newer not in code, newer
