# IntuneAppFastDeploy — notes for Claude

A small Docker web tool that publishes and updates Win32 applications (MSI/EXE) in Microsoft Intune from a download link, shows the software found on the devices, and removes or replaces software through Intune. Public repository, MIT. The maintainer works alone on it and writes in French: answer in French, keep code, comments and commit messages in English.

**Status: test version.** Nothing has ever been run against a real Intune tenant or a real Microsoft sign-in. Everything is tested against an in-memory fake (`tests/fake_intune.py`), and the PowerShell scripts on real Windows in CI. Say so plainly whenever a change touches a Graph call; never present such a change as verified.

## Commands

```bash
uv sync                                          # install the locked dependencies
uv run pytest                                    # all Python tests
uv run ruff check . && uv run ruff format .      # lint and format (CI checks both)
uv run python -m tests.demo                      # the interface on http://localhost:8080, fake Intune, no sign-in
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build   # run the image built from this checkout
```

Screenshots of the README (needs Node.js and Playwright; restart the demo before each language so it starts from the same data):

```bash
node docs/screenshots/make.js en
node docs/screenshots/make.js fr
```

The PowerShell tests (`tests/windows/`, Pester) only run on Windows; in CI they run in the `windows-scripts` job.

## Layout

| Path | Role |
|---|---|
| `src/iafd/download.py`, `msi.py`, `exe.py`, `analyze.py` | Fetch an installer, read its metadata without Windows |
| `src/iafd/intunewin.py` | Build `.intunewin` packages (zip, AES-256-CBC, HMAC) |
| `src/iafd/appmodel.py` | Pure functions building the Graph payloads for Win32 apps |
| `src/iafd/graph.py` | Every Microsoft Graph call (beta endpoint) |
| `src/iafd/publish.py` | Workflows: new app, supersedence, in-place update, promotion, removals |
| `src/iafd/inventory.py` | Grouping, search and paging of the device inventory |
| `src/iafd/removal.py`, `protected.py` | Removal settings, script rendering, protected software |
| `src/iafd/scripts/*.ps1` | Scripts run on the devices by Intune |
| `src/iafd/auth.py`, `web.py`, `jobs.py`, `store.py`, `config.py` | Sign-in, HTTP API, background jobs, local JSON stores, settings |
| `src/iafd/static/`, `src/iafd/locales/` | Single-page interface (vanilla JS, no build step) and its texts |
| `tests/demo.py`, `tests/fake_intune.py` | Demo server and the fake tenant used by tests and screenshots |

## Rules this project keeps

- **Texts**: every string shown to a person lives in `src/iafd/locales/en.json` and `fr.json`, same keys in both. `tests/test_locales.py` fails when a key is missing. Errors raised by the server carry a translation key (`error.…`), not a sentence.
- **Documentation**: `README.md` (English) and `README.fr.md` (French) are kept in step, as detailed as possible. `SECURITY.md` is bilingual in one file. Update the screenshots when a screen changes.
- **No tenant data in the repository.** Settings go in the `environment:` block of `docker-compose.yml`; local state goes in `./config` (a bind-mounted folder, not a Docker volume): `rollout.json`, `removals.json`, `protected.txt`. Nothing is written in the Notes field of Intune applications.
- **Optional features are off by default** and each adds its own delegated permissions (`ENABLE_GROUP_PICKER`, `ENABLE_DEVICE_INVENTORY`, `ENABLE_REMEDIATIONS`, `ENABLE_DEVICE_GROUPS`). A scope that has not been consented breaks sign-in, so never add a scope unconditionally.
- **Delegated access only**: the tool acts as the signed-in administrator, stores no secret, keeps tokens in memory.
- **Scripts for the devices** target Windows PowerShell 5.1 (no `??`, `?.`, ternary, `&&`). Settings are embedded as base64-encoded JSON: nothing typed by a person is ever assembled into code. Every branch has a Pester test.
- **Removals are guarded on the server**, not only in the interface: protected list, exact names only, typed confirmation. The script never guesses a silent switch.
- **Identifiers placed in a Graph URL or filter are validated** (`inventory.safe_id`, GUID checks).
- **Dependencies**: version ranges in `pyproject.toml`, exact versions in `uv.lock`. Renovate opens pull requests and never merges; the maintainer merges them.
- **Graph calls** are checked against the Microsoft Graph beta reference before being written; the pages used are listed in the README (Development → Microsoft Graph reference).

## Git and GitHub

- `main` is protected: changes go through a pull request with the `test` and `image` checks green and no high-severity CodeQL alert.
- Open pull requests as drafts. Do not merge without the maintainer asking for it.
- A pull request says what was tested and, separately, what was not verified.

## What only a real tenant can settle

Suggested order for the first real trials, each on something unimportant:

1. Sign-in, then the application list.
2. Publishing one application; check the result in the Intune portal. The `.intunewin` download is the fallback if the upload is refused.
3. The device inventory alone: are the names what the registry of the devices shows?
4. One removal on one or two devices: how Intune handles an Uninstall assignment with a script detection rule, and whether `deviceStatuses` still answers (the interface falls back to a link to the portal).
5. The on-demand removal and the device groups, which need more permissions.

Known open points: the value `1607` for `minimumSupportedWindowsRelease` on new applications; creating a content version with an empty body; the portal URL built for a removal rule.
