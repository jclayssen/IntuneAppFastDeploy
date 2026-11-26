# IntuneAppFastDeploy

**English** · [Français](README.fr.md)

Publish and update Windows applications (MSI / EXE) in Microsoft Intune from a download link.

You paste the link of the new installer, the tool downloads it, reads what it can from the file, pre-fills everything from the application that already exists in Intune, and you click **Publish**. Nothing is sent to Intune before you confirm.

> ⚠️ **Test version.** This tool has not yet been validated against a real Intune tenant: it is published for testing, not for production use. The packaging, the workflows and the interface are covered by automated tests, and the requests sent to Intune were checked against Microsoft's API documentation, but the tests run against a simulated Intune. Do your first publication on an unimportant application and check the result in the Intune portal. If something fails, the **Download the .intunewin** button gives you a package you can upload by hand, and an issue with the message shown is welcome.

## Contents

- [What it does](#what-it-does)
- [What you need](#what-you-need)
- [Step 1 — Create the app registration in Microsoft Entra ID](#step-1--create-the-app-registration-in-microsoft-entra-id)
- [Step 2 — Install on your PC](#step-2--install-on-your-pc)
- [Step 3 — Use it](#step-3--use-it)
- [Pilot and production groups](#pilot-and-production-groups)
- [Running on a server with HTTPS](#running-on-a-server-with-https)
- [Who can sign in](#who-can-sign-in)
- [Keeping the tool up to date](#keeping-the-tool-up-to-date)
- [Settings reference](#settings-reference)
- [Privacy and security](#privacy-and-security)
- [Troubleshooting](#troubleshooting)
- [Limits](#limits)
- [Development](#development)
- [Adding a language](#adding-a-language)

## What it does

- **Update an application**: paste the link of the new installer. Install command, uninstall command, detection rule, description, icon and requirements are copied from the current application; only what changes with the version is updated.
- **Two update methods**, chosen at each publication:
  - *New application that replaces the current one* (supersedence, Microsoft's recommended method). At most two versions are kept: when a third one is published, the oldest is deleted from Intune after the new one has been published successfully.
  - *Modify the existing application*: the installer is replaced in place; assignments do not change.
- **Pilot, then production**: a new version goes to the pilot groups first; a **Go to production** button adds the production groups.
- **New application**: same screen, you fill in the fields once. For an MSI everything is read from the file. For an EXE the tool recognizes common installers (Inno Setup, NSIS, WiX Burn, InstallShield, Advanced Installer, Squirrel) and proposes the silent switch.
- **Creates the `.intunewin` on Linux**, without Microsoft's Windows-only tool.
- **Works with any tenant**: nothing about a tenant is stored in this repository or in the image. Each organization uses its own app registration.
- **English and French interface**, one file per language.

## What you need

- A machine with **Docker** (Docker Desktop on Windows/macOS, Docker Engine with Compose 2.24 or later on Linux) and Internet access.
- In Microsoft Entra ID: the right to create an app registration and to **grant admin consent** (Global Administrator, Privileged Role Administrator or Cloud Application Administrator), once.
- For each person who uses the tool: an Intune role that can manage apps (**Intune Administrator** or **Application Manager**). The tool acts with the rights of the signed-in person and can never do more than they can do in the Intune portal.

## Step 1 — Create the app registration in Microsoft Entra ID

This tells Microsoft that the tool exists in *your* tenant. It takes about five minutes and is done once.

1. Open <https://entra.microsoft.com> and go to **Identity → Applications → App registrations → New registration**.
2. **Name**: `IntuneAppFastDeploy` (any name works).
3. **Supported account types**: *Accounts in this organizational directory only (single tenant)*.
4. **Redirect URI**: choose the platform **Public client/native (mobile & desktop)** and enter:

   ```
   http://localhost:8080/auth/callback
   ```

5. Click **Register**.
6. On the **Overview** page, copy the two values you will need in step 2:
   - **Application (client) ID** → `CLIENT_ID`
   - **Directory (tenant) ID** → `TENANT_ID`
7. Go to **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, search for `DeviceManagementApps.ReadWrite.All`, tick it and click **Add permissions**.
8. Click **Grant admin consent for \<your organization\>** and confirm. The status column must show a green tick.

No client secret is needed: the registration is a *public client* and each person signs in with their own account.

**Optional — search groups by name.** By default you paste the Object ID of a group. To search groups by name instead, also add the delegated permission `GroupMember.Read.All`, grant admin consent again, and set `ENABLE_GROUP_PICKER: "true"` in `docker-compose.yml`.

## Step 2 — Install on your PC

One file is enough: `docker-compose.yml`. There is nothing to clone or build.

1. Create an empty folder and download the file into it:

   ```bash
   curl -O https://raw.githubusercontent.com/jclayssen/IntuneAppFastDeploy/main/docker-compose.yml
   ```

   On Windows PowerShell:

   ```powershell
   Invoke-WebRequest -Uri https://raw.githubusercontent.com/jclayssen/IntuneAppFastDeploy/main/docker-compose.yml -OutFile docker-compose.yml
   ```

   You can also open the file in your browser and save it, or copy its content.

2. Open `docker-compose.yml` in a text editor and paste the two values from step 1 between the quotes:

   ```yaml
   TENANT_ID: "00000000-0000-0000-0000-000000000000"
   CLIENT_ID: "11111111-1111-1111-1111-111111111111"
   ```

3. **Linux only**: create the configuration folder yourself, so that it belongs to you and not to root:

   ```bash
   mkdir config
   ```

   If your user is not number 1000 (check with `id -u`), also change the line `user: "1000:1000"` in `docker-compose.yml`.

4. Start the tool:

   ```bash
   docker compose up -d
   ```

5. Open <http://localhost:8080> and click **Sign in with Microsoft**.

The `config` folder next to `docker-compose.yml` holds the pilot/production groups (`config/rollout.json`). It is a plain folder on your disk, not a Docker volume: back it up like any other file.

After changing a setting in `docker-compose.yml`, run `docker compose up -d` again. To stop the tool:

```bash
docker compose down
```

## Step 3 — Use it

### Update an application

1. In the list, click **Update** on the application.
2. Paste the download link of the new installer (or choose a file from your computer) and click **Download and analyze**.
3. Check the review screen:
   - **Detected in the file**: file name, size, installer type and SHA-256 fingerprint. Compare the fingerprint with the one published by the vendor when there is one.
   - **Application**: name, version, commands. The setup file name inside the install command is replaced automatically.
   - **Detection rule**: how Intune knows the new version is installed. An MSI product code or a "version greater than or equal to" rule is moved to the new version automatically. Read the yellow notes when there are any.
   - **How to update**: the method, and who receives the new version first.
4. Click **Publish to Intune**. The progress is shown step by step.

The link must return the installer itself. A page where you still have to click a button is refused with the message *"The link does not return an MSI or EXE file"*: copy the link of the final download button instead.

### Add a new application

Click **New application**, give the link, then fill in:

- **Install command**: proposed automatically for MSI files and for recognized EXE installers. Otherwise add the silent switch given by the vendor (often `/S`, `/silent` or `/quiet`).
- **Uninstall command**: automatic for an MSI. For an EXE it cannot be read from the file; the screen shows the usual form for the recognized installer.
- **Detection rule**: for an EXE, the simplest reliable rule is *File*, with the folder and name of the main program and *Version greater than or equal to*.
- **Pilot and production groups**, and who receives the application now.

### The two update methods

| | New application that replaces the current one | Modify the existing application |
|---|---|---|
| What happens in Intune | A new application is created with a supersedence link to the current one | The installer of the same application is replaced |
| Going back | The previous version is still there | Not possible |
| Pilot phase | Yes | No |
| Old versions | Two kept; the oldest is deleted when a third is published | Nothing to clean up |

Deleting the oldest version is announced on the review screen, with a checkbox to keep it. It happens only after the new version has been published successfully, and it cannot be undone.

## Pilot and production groups

Open an application (click its name) to configure, per application:

- **Pilot**: the groups that receive a new version first.
- **Production**: the groups added when you click **Go to production**. If you leave it empty, the assignments of the previous version are reused.

For each one you choose *Required* or *Available*, and add groups:

- **All devices** / **All users**: one click.
- **A group**: paste its **Object ID**. You find it in the Entra admin center under **Groups → \<the group\> → Overview → Object ID**. The label is only a reminder for you.
- With `ENABLE_GROUP_PICKER: "true"` (see step 1), a search box finds groups by name.

When you publish a new version you choose *The pilot groups first*, *Production directly* or *Nobody for now*. During the pilot phase the application page shows the **Pilot** badge and the **Go to production** button.

These settings follow the application from one version to the next. They are stored in `config/rollout.json` only — nothing is written in the Notes field or anywhere else in Intune.

## Running on a server with HTTPS

Away from `localhost`, Microsoft sign-in requires HTTPS. The `https` profile starts a small web server (Caddy) in front of the tool.

1. In `docker-compose.yml`, set the address in the `app` service and the name and certificate in the `https` service:

   ```yaml
   PUBLIC_URL: "https://intune-deploy.example.com"
   ```

   ```yaml
   SITE_ADDRESS: "intune-deploy.example.com"
   TLS: "internal"
   ```

   `TLS` chooses the certificate:

   | `TLS:` | Certificate | Requirements |
   |---|---|---|
   | `"internal"` | Self-signed, generated automatically | None. Browsers show a warning until you trust the certificate (see below). |
   | `"/certs/cert.pem /certs/key.pem"` | Your own certificate | Put the certificate (with its chain) and the private key in a `certs` folder next to `docker-compose.yml`, under these names. |
   | `"you@example.com"` | Let's Encrypt, renewed automatically | `SITE_ADDRESS` must be a public DNS name pointing to the server, and ports 80 and 443 must be reachable from the Internet. |

2. In the Entra app registration (**Authentication → Mobile and desktop applications → Add URI**), add:

   ```
   https://intune-deploy.example.com/auth/callback
   ```

3. Start with the profile:

   ```bash
   docker compose --profile https up -d
   ```

4. Open `https://intune-deploy.example.com`.

**Trusting the self-signed certificate.** With `TLS: "internal"`, the authority certificate is written to `config/caddy/caddy/pki/authorities/local/root.crt`. Import it on the colleagues' PCs as a *Trusted Root Certification Authority* (you can deploy it with Intune) and the warning disappears.

**Do not expose the tool to the Internet unless you have to.** It lets signed-in administrators push software to every device. Let's Encrypt needs the server to be reachable from the Internet; on an internal network prefer your own certificate or the self-signed one.

No other file is needed: the configuration of the HTTPS front door is inside `docker-compose.yml`.

## Who can sign in

Only accounts of your tenant can sign in, and Intune enforces each person's own rights. To restrict further, either:

- set `ALLOWED_USERS: "first@example.com,second@example.com"` in `docker-compose.yml`, or
- in the Entra admin center, open **Enterprise applications → IntuneAppFastDeploy → Properties**, set **Assignment required?** to **Yes**, then add the allowed people under **Users and groups**.

Sessions live in the memory of the container: restarting it signs everyone out.

## Keeping the tool up to date

On the machine that runs the tool, one command fetches the latest image and restarts:

```bash
docker compose pull && docker compose up -d
```

You can schedule it (Windows Task Scheduler, cron) if you want it to happen without you.

How new images are produced, for whoever maintains the repository (or a fork):

- Dependencies are declared as minimum versions in `pyproject.toml`. The exact versions that passed the tests are recorded in `uv.lock`, a generated file nobody edits by hand.
- **[Renovate](https://github.com/apps/renovate)** (a free bot, installed on the repository in one click) opens a pull request when a dependency, the base image or a GitHub Action has a new version, and a weekly one that refreshes `uv.lock`. A dependency with a known security flaw gets a pull request immediately, labelled `security`. **Renovate never merges**: someone reviews the pull request, sees that the tests are green, and merges it.
- Each merge into `main` publishes a new image. The image is also rebuilt every Monday so the base system receives its security patches even when nothing else changed.
- **CodeQL** scans the tool's own code on each change.

For Renovate's security pull requests, enable **Settings → Code security → Dependabot alerts** on the repository.

What still needs a person: merging the pull requests, looking at one whose tests fail, and running the update command on the server.

## Settings reference

All settings go in the `environment:` section of the `app` service in `docker-compose.yml`. Run `docker compose up -d` after a change.

| Setting | Default | Meaning |
|---|---|---|
| `TENANT_ID` | — | Directory (tenant) ID. Required. |
| `CLIENT_ID` | — | Application (client) ID. Required. |
| `PUBLIC_URL` | `http://localhost:8080` | Address typed in the browser. The redirect URI is `PUBLIC_URL` + `/auth/callback`. |
| `DEFAULT_LANGUAGE` | `en` | Language used when the browser's language is not available. |
| `ENABLE_GROUP_PICKER` | `false` | Search groups by name (needs `GroupMember.Read.All`). |
| `ALLOWED_USERS` | empty | Comma-separated sign-in names allowed to use the tool. Empty: anyone in the tenant. |
| `MAX_DOWNLOAD_MB` | `8192` | Largest installer accepted. |
| `SESSION_HOURS` | `12` | Hours before a new sign-in is required. |
| `CLIENT_SECRET` | empty | Only for a registration of type *Web* with a secret. |
| `AUTHORITY_HOST` / `GRAPH_URL` | Microsoft public cloud | For national clouds. Not tested. |

Outside `environment:`, three other lines of `docker-compose.yml` can be adapted: `user:` (owner of the `config` folder on Linux), `ports:` (local port; if you change it, change `PUBLIC_URL` and the redirect URI too), and `SITE_ADDRESS` / `TLS` in the `https` service.

## Privacy and security

- **No telemetry.** The tool contacts only Microsoft (sign-in, Microsoft Graph, the Azure storage Intune designates for the upload) and the download links you give it.
- **Nothing about your tenant leaves your machine or is stored in this repository.** Your identifiers live in your own copy of `docker-compose.yml`, and your groups in your `config` folder.
- **No password or secret is stored.** Sign-in uses the authorization code flow with PKCE; tokens stay in memory.
- **Delegated rights only.** Every action is done as the signed-in person and appears under their name in the Intune audit logs.
- **Installers are downloaded by the server.** Anyone who can sign in can make the server fetch a URL, including internal ones: keep access limited to administrators.
- The container runs as an unprivileged user with all Linux capabilities dropped.

To report a security problem, see the [security policy](SECURITY.md): use the repository's **Security → Report a vulnerability** page rather than a public issue.

## Troubleshooting

| Message or symptom | Cause and fix |
|---|---|
| *Configuration needed* at startup | `TENANT_ID` or `CLIENT_ID` is empty in `docker-compose.yml`. Fill them in and run `docker compose up -d` again. |
| `AADSTS50011` (redirect URI mismatch) | The address in the browser and `PUBLIC_URL` must match a redirect URI of the registration exactly, including `http`/`https` and the port. |
| `AADSTS7000218` (client secret required) | The redirect URI was added under the *Web* platform. Move it to **Mobile and desktop applications**, or set `CLIENT_SECRET`. |
| `AADSTS65001` (consent required) | Admin consent was not granted (step 1, point 8). |
| *Microsoft sign-in cannot be reached* | Wrong `TENANT_ID`, or the container has no Internet access. |
| *Your account does not have the rights for this action in Intune* | The signed-in person needs the Intune Administrator or Application Manager role. |
| *The link does not return an MSI or EXE file* | The link leads to a web page or needs a sign-in. Download the file yourself and use **choose a file**. |
| *Intune refused the request* | The detail after the dash is Intune's own message. Use **Download the .intunewin** to upload by hand, and please open an issue with that detail. |
| *The config folder cannot be written* (red notice, Linux) | Docker created the folder as root. Give it to the user of the `user:` line: `sudo chown -R 1000:1000 config`. |
| `docker compose up` cannot pull the image | Check the Internet access of the machine and that `ghcr.io` is not blocked by a proxy or firewall. |
| The application stays "not ready" in Intune | Intune processes the package for a few minutes after the upload. |

Logs:

```bash
docker compose logs -f app
```

## Limits

- Windows Win32 applications only (not Microsoft Store, macOS or mobile apps).
- One setup file per application. An installer that needs several files next to it must be wrapped into a single EXE or MSI first.
- The uninstall command and the detection rule of an EXE cannot be guessed from the file: you enter them once, then they are copied at each update.
- Detection rules based on a script are copied as they are.
- The oldest version is deleted from Intune when a third one is published (unless you untick the box).

## Development

Requirements: [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format .
```

Build and run the image from your checkout instead of the published one:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Run the interface against a simulated Intune, without sign-in (development only):

```bash
uv run python -m tests.demo
```

Layout:

| Path | Role |
|---|---|
| `src/iafd/download.py`, `msi.py`, `exe.py`, `analyze.py` | Fetch an installer and read its metadata |
| `src/iafd/intunewin.py` | Build (and unpack) `.intunewin` packages |
| `src/iafd/appmodel.py` | Build the Microsoft Graph payloads (pure functions) |
| `src/iafd/graph.py`, `publish.py` | Microsoft Graph calls and the publishing workflows |
| `src/iafd/auth.py`, `web.py`, `jobs.py`, `store.py` | Sign-in, HTTP API, background tasks, local configuration |
| `src/iafd/static/`, `src/iafd/locales/` | Interface and its texts |

### Microsoft Graph reference

The tool uses the beta endpoint of Microsoft Graph. Pages the code was checked against: [win32LobApp](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-win32lobapp?view=graph-rest-beta), [win32LobAppFileSystemRule](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-win32lobappfilesystemrule?view=graph-rest-beta), [win32LobAppRegistryRule](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-win32lobappregistryrule?view=graph-rest-beta), [win32LobAppProductCodeRule](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-win32lobappproductcoderule?view=graph-rest-beta), [mobileAppContentFile](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-mobileappcontentfile?view=graph-rest-beta), [commit](https://learn.microsoft.com/en-us/graph/api/intune-apps-mobileappcontentfile-commit?view=graph-rest-beta), [renewUpload](https://learn.microsoft.com/en-us/graph/api/intune-apps-mobileappcontentfile-renewupload?view=graph-rest-beta), [assign](https://learn.microsoft.com/en-us/graph/api/intune-apps-mobileapp-assign?view=graph-rest-beta), [win32LobAppAssignmentSettings](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-win32lobappassignmentsettings?view=graph-rest-beta), [mobileAppSupersedence](https://learn.microsoft.com/en-us/graph/api/resources/intune-apps-mobileappsupersedence?view=graph-rest-beta), [updateRelationships](https://learn.microsoft.com/en-us/graph/api/intune-apps-mobileapp-updaterelationships?view=graph-rest-beta).

## Adding a language

1. Copy `src/iafd/locales/en.json` to `src/iafd/locales/<code>.json` (for example `de.json`).
2. Translate the values. Keep the keys and the `{placeholders}` unchanged, and set `_name` to the name of the language as it should appear in the menu.
3. Run `uv run pytest`: a test checks that no text is missing.

The language appears in the menu automatically.

## License

[MIT](LICENSE)
