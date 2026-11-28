// IntuneAppFastDeploy - single page interface, no build step, no dependency.
// All texts come from /locales/<language>.json: adding a language = adding a file.

const main = document.getElementById("main");
let config = {};
let section = "apps";
let strings = {};
let fallback = {};

// ---------------------------------------------------------------- helpers

function t(key, vars = {}) {
  let text = strings[key] ?? fallback[key] ?? key;
  for (const [name, value] of Object.entries(vars)) text = text.replaceAll(`{${name}}`, value);
  return text;
}

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function show(...nodes) {
  main.replaceChildren(...nodes.flat().filter(Boolean));
  window.scrollTo(0, 0);
}

class ApiError extends Error {
  constructor(code, detail) {
    super(code);
    this.code = code;
    this.detail = detail || "";
  }
}

function errorText(error) {
  const code = error.code || "error.unexpected";
  const detail = error.detail || (error.code ? "" : error.message);
  return detail ? `${t(code)} — ${detail}` : t(code);
}

function errorBox(error) {
  return h("div", { class: "notice error", role: "alert" }, errorText(error));
}

async function api(method, url, body) {
  const options = { method, headers: { "X-IAFD": "1" } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ApiError("error.network");
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    const error = data.error || {};
    if (response.status === 401) {
      showLogin(error.code === "error.session_expired" ? error.code : null);
    }
    throw new ApiError(error.code || "error.http", error.detail);
  }
  return data;
}

function bytes(value) {
  if (value == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function field(label, control, hint) {
  return [
    h("label", { class: "field" }, h("span", {}, label), control),
    hint ? h("p", { class: "hint" }, hint) : null,
  ];
}

function input(value, attrs = {}) {
  return h("input", { type: "text", value: value ?? "", spellcheck: false, ...attrs });
}

function select(options, value, attrs = {}) {
  const node = h(
    "select",
    attrs,
    options.map(([key, label]) => h("option", { value: key }, label)),
  );
  node.value = value;
  return node;
}

function button(label, onclick, cls = "") {
  return h("button", { type: "button", class: cls, onclick }, label);
}

async function busy(node, action) {
  if (node) node.disabled = true;
  try {
    return await action();
  } finally {
    if (node) node.disabled = false;
  }
}

// ---------------------------------------------------------------- language

async function loadLanguage(code) {
  const response = await fetch(`/locales/${code}.json`);
  return response.ok ? response.json() : {};
}

async function setLanguage(code) {
  if (!config.languages[code]) code = config.languages[config.default_language] ? config.default_language : "en";
  strings = await loadLanguage(code);
  document.documentElement.lang = code;
  try {
    localStorage.setItem("iafd_language", code);
  } catch {
    /* storage unavailable */
  }
  document.getElementById("language").value = code;
  document.getElementById("logout").textContent = t("action.sign_out");
  document.getElementById("test-version").textContent = t("footer.test_version");
  document.getElementById("test-version").title = t("footer.test_version_hint");
  document.getElementById("footer").textContent = t("footer.privacy", { version: config.version });
}

function preferredLanguage() {
  try {
    const saved = localStorage.getItem("iafd_language");
    if (saved) return saved;
  } catch {
    /* storage unavailable */
  }
  const browser = (navigator.language || "").slice(0, 2).toLowerCase();
  return config.languages[browser] ? browser : config.default_language;
}

// ---------------------------------------------------------------- sign-in

function showLogin(reason) {
  document.getElementById("nav").replaceChildren();
  document.getElementById("logout").hidden = true;
  document.getElementById("user").textContent = "";
  if (!config.configured) {
    show(
      h(
        "div",
        { class: "panel" },
        h("h1", {}, t("setup.title")),
        h("p", {}, t("setup.text")),
        h("p", { class: "mono" }, `${t("setup.redirect")} ${config.redirect_uri}`),
      ),
    );
    return;
  }
  show(
    h(
      "div",
      { class: "panel" },
      h("h1", {}, t("login.title")),
      h("p", { class: "muted" }, t("login.text")),
      reason ? h("div", { class: "notice error", role: "alert" }, t(reason)) : null,
      h("a", { class: "btn", href: "/auth/login" }, t("action.sign_in")),
    ),
  );
}

// ---------------------------------------------------------------- app list

// The two sections of the tool. Only shown when the device inventory is enabled.
function drawNav(current) {
  if (current) section = current;
  const nav = document.getElementById("nav");
  if (!config.device_inventory) {
    nav.replaceChildren();
    return;
  }
  const tab = (key, label, open) =>
    h("button", { type: "button", class: `tab ${section === key ? "current" : ""}`, "aria-current": section === key ? "page" : null, onclick: open }, label);
  nav.replaceChildren(
    tab("apps", t("nav.apps"), showApps),
    tab("inventory", t("nav.inventory"), () => showInventory("software")),
    tab("removals", t("nav.removals"), showRemovals),
  );
}

async function showApps() {
  drawNav("apps");
  const list = h("div", { class: "panel" }, h("p", { class: "muted" }, t("apps.loading")));
  const search = h("input", { type: "search", placeholder: t("apps.search"), class: "grow" });
  show(
    h(
      "div",
      { class: "head" },
      h("h1", {}, t("apps.title")),
      search,
      button(t("action.new_app"), () => showWizard(null)),
    ),
    config.config_writable ? null : h("div", { class: "notice error", role: "alert" }, t("error.config_not_writable")),
    list,
  );
  let apps;
  try {
    apps = (await api("GET", "/api/apps")).apps;
  } catch (error) {
    list.replaceChildren(errorBox(error));
    return;
  }
  const draw = () => {
    const query = search.value.trim().toLowerCase();
    const rows = apps.filter((app) => `${app.name} ${app.publisher}`.toLowerCase().includes(query));
    if (!rows.length) {
      list.replaceChildren(h("p", { class: "empty muted" }, t(apps.length ? "apps.no_match" : "apps.empty")));
      return;
    }
    list.replaceChildren(
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h("thead", {}, h("tr", {}, [t("apps.name"), t("apps.version"), t("apps.publisher"), t("apps.status"), ""].map((x) => h("th", {}, x)))),
          h(
            "tbody",
            {},
            rows.map((app) =>
              h(
                "tr",
                {},
                h("td", {}, button(app.name, () => showApp(app.id), "linkish")),
                h("td", {}, app.version || "—"),
                h("td", {}, app.publisher),
                h(
                  "td",
                  {},
                  h("span", { class: `badge ${app.assigned ? "ok" : ""}` }, t(app.assigned ? "apps.assigned" : "apps.unassigned")),
                  app.superseded_count ? [" ", h("span", { class: "badge" }, t("apps.superseded"))] : null,
                ),
                h("td", { class: "actions" }, button(t("action.update"), () => showWizard(app.id), "small")),
              ),
            ),
          ),
        ),
      ),
    );
  };
  search.addEventListener("input", draw);
  draw();
}

// ---------------------------------------------------------------- group editor

function targetLabel(target) {
  if (target.type === "allDevices") return t("target.all_devices");
  if (target.type === "allUsers") return t("target.all_users");
  return target.name ? `${target.name} (${target.id})` : target.id;
}

// The guides carry a little markup: `code`, **a label of the interface** and https links.
// Built as nodes, never as HTML.
function rich(text) {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|https:\/\/[^\s)]+)/).map((part) => {
    if (/^`.+`$/.test(part)) return h("code", { class: "ge-code" }, part.slice(1, -1));
    if (/^\*\*.+\*\*$/.test(part)) return h("strong", {}, part.slice(2, -2));
    if (part.startsWith("https://")) return h("a", { href: part, target: "_blank", rel: "noopener noreferrer" }, part.slice(8));
    return part;
  });
}

// Short explanations, one per line; what is absent is skipped.
function notes(...texts) {
  return h("ul", { class: "ge-notes" }, texts.filter(Boolean).map((text) => h("li", {}, rich(text))));
}

// The explanation of the choice a select shows, kept in step with it.
function explained(control, textFor, id) {
  const hint = h("p", { class: "hint ge-hint", id });
  const sync = () => hint.replaceChildren(...rich(textFor(control.value)));
  control.setAttribute("aria-describedby", id);
  control.addEventListener("change", sync);
  sync();
  return hint;
}

// Enabling the search of groups by name, in the order that keeps sign-in working.
const GROUP_SETUP_STEPS = ["groups.setup_step1", "groups.setup_step2", "groups.setup_step3", "groups.setup_step4", "groups.setup_step5"];
const GROUP_ID_SHAPE = "00000000-0000-0000-0000-000000000000";
let groupEditors = 0;

// Shown where the search would be when ENABLE_GROUP_PICKER is off: says it exists and how to enable it.
function groupSearchOff(uid) {
  return h(
    "div",
    { class: "ge-part ge-off" },
    h("div", { class: "ge-head" }, h("label", { class: "ge-label", for: `${uid}-search` }, t("rollout.search_group")), h("span", { class: "badge" }, t("groups.search_off"))),
    h("input", { type: "search", id: `${uid}-search`, disabled: true, placeholder: t("groups.search_placeholder"), "aria-describedby": `${uid}-search-help` }),
    h("p", { class: "ge-help", id: `${uid}-search-help` }, t("groups.search_teaser")),
    h(
      "details",
      { class: "ge-more" },
      h("summary", {}, t("groups.setup_show")),
      h(
        "div",
        { class: "ge-guide" },
        h("p", {}, t("groups.setup_intro")),
        h("ol", {}, GROUP_SETUP_STEPS.map((key) => h("li", {}, rich(t(key))))),
        h("p", { class: "notice" }, rich(t("groups.setup_order"))),
        h("p", { class: "ge-help" }, rich(t("groups.setup_scope"))),
      ),
    ),
  );
}

function groupSearch(uid, add) {
  const results = h("div", { class: "results ge-results", "aria-live": "polite" });
  const query = h("input", { type: "search", id: `${uid}-search`, placeholder: t("groups.search_placeholder"), autocomplete: "off", "aria-describedby": `${uid}-search-help` });
  let timer;
  let request = 0;
  query.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++request;
      results.replaceChildren(h("span", { class: "muted" }, t("groups.searching")));
      try {
        const found = (await api("GET", `/api/groups?q=${encodeURIComponent(query.value)}`)).groups;
        if (mine !== request) return;
        results.replaceChildren(
          ...(found.length
            ? found.map((group) =>
                h(
                  "button",
                  { type: "button", class: "linkish ge-result", onclick: () => add({ type: "group", id: group.id, name: group.name }) },
                  h("span", {}, group.name),
                  h("span", { class: "mono muted" }, group.id),
                ),
              )
            : [h("span", { class: "muted" }, t("rollout.no_result"))]),
        );
      } catch (error) {
        if (mine === request) results.replaceChildren(errorBox(error));
      }
    }, 300);
  });
  return h(
    "div",
    { class: "ge-part" },
    h("label", { class: "ge-label", for: `${uid}-search` }, t("rollout.search_group")),
    query,
    h("p", { class: "ge-help", id: `${uid}-search-help` }, t("groups.search_hint")),
    results,
  );
}

function stageEditor(title, hint, stage, options = {}) {
  const uid = `ge-${++groupEditors}`;
  const state = { intent: stage?.intent || "required", targets: [...(stage?.targets || [])] };
  const chips = h("div", { class: "chips", role: "group", tabIndex: -1, "aria-labelledby": `${uid}-chosen` });
  const message = h("div", { role: "alert", id: `${uid}-error` });
  const status = h("p", { class: "ge-status", role: "status" });
  const draw = () => {
    chips.replaceChildren(
      ...(state.targets.length
        ? state.targets.map((target, index) =>
            h(
              "span",
              { class: "chip" },
              targetLabel(target),
              h("button", { type: "button", "aria-label": `${t("action.remove")} — ${targetLabel(target)}`, onclick: () => remove(index) }, "×"),
            ),
          )
        : [h("span", { class: "muted" }, t("rollout.no_group"))]),
    );
  };
  // The removed chip had the focus: hand it to its neighbour, or to the list when it is empty.
  const remove = (index) => {
    state.targets.splice(index, 1);
    status.textContent = "";
    draw();
    const buttons = chips.querySelectorAll("button");
    (buttons[Math.min(index, buttons.length - 1)] || chips).focus();
  };
  // Returns false when the target is refused, so that what was typed is kept.
  const add = (target) => {
    message.replaceChildren();
    status.textContent = "";
    const key = target.type === "group" ? target.id.toLowerCase() : target.type;
    if (target.type === "group" && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(target.id)) {
      message.replaceChildren(h("div", { class: "notice error" }, t("error.bad_group_id")));
      return false;
    }
    const known = state.targets.some((item) => (item.type === "group" ? item.id.toLowerCase() : item.type) === key);
    if (!known) state.targets.push(target);
    draw();
    status.textContent = t(known ? "groups.already" : "groups.added", { name: target.name || targetLabel(target) });
    return true;
  };
  const intent = select([["required", t("intent.required")], ["available", t("intent.available")]], state.intent, {
    onchange: (event) => { state.intent = event.target.value; },
  });
  const groupId = input("", { id: `${uid}-id`, placeholder: GROUP_ID_SHAPE, class: "mono ge-id", autocomplete: "off", "aria-describedby": `${uid}-error`, oninput: (event) => event.target.removeAttribute("aria-invalid") });
  const groupName = input("", { id: `${uid}-name`, placeholder: t("groups.label_placeholder"), class: "ge-name", autocomplete: "off", "aria-describedby": `${uid}-name-help` });
  const addManual = () => {
    const added = add({ type: "group", id: groupId.value.trim(), name: groupName.value.trim() });
    if (!added) groupId.setAttribute("aria-invalid", "true");
    if (added) {
      groupId.value = "";
      groupName.value = "";
    }
    groupId.focus();
  };
  for (const control of [groupId, groupName]) {
    control.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addManual();
    });
  }
  const manual = h(
    "div",
    { class: "ge-part" },
    h(
      "div",
      { class: "ge-fields" },
      h("div", { class: "ge-field" }, h("label", { class: "ge-label", for: `${uid}-id` }, t("rollout.group_id")), groupId),
      h("div", { class: "ge-field" }, h("label", { class: "ge-label", for: `${uid}-name` }, t("rollout.group_name")), groupName),
      button(t("action.add_group"), addManual, "ghost"),
    ),
    message,
    h("p", { class: "ge-help", id: `${uid}-name-help` }, t("groups.label_hint")),
    h("details", { class: "ge-more" }, h("summary", {}, t("groups.find_id_show")), h("p", { class: "ge-guide" }, rich(t("groups.find_id_text")))),
  );
  const everyone = h(
    "div",
    { class: "ge-part" },
    h("span", { class: "ge-label", id: `${uid}-everyone` }, t("groups.builtin_title")),
    h(
      "div",
      { class: "row", role: "group", "aria-labelledby": `${uid}-everyone` },
      button(t("target.all_devices"), () => add({ type: "allDevices" }), "ghost small"),
      button(t("target.all_users"), () => add({ type: "allUsers" }), "ghost small"),
    ),
    notes(t("groups.all_devices_hint"), t("groups.all_users_hint")),
  );
  draw();
  const node = h(
    "div",
    { class: "stage ge" },
    h("h3", {}, title),
    hint ? h("p", { class: "hint" }, hint) : null,
    options.noIntent ? null : [...field(t("rollout.intent"), intent), notes(t("groups.intent_required_hint"), t("groups.intent_available_hint"))],
    h("span", { class: "ge-label", id: `${uid}-chosen` }, t("groups.selected")),
    chips,
    status,
    h(
      "fieldset",
      { class: "ge-add" },
      h("legend", {}, t("groups.add_title")),
      config.group_picker ? groupSearch(uid, add) : groupSearchOff(uid),
      // With the search, typing an identifier is the exception: it stays folded.
      config.group_picker ? h("details", { class: "ge-more" }, h("summary", {}, t("rollout.manual")), manual) : manual,
      everyone,
    ),
  );
  return { node, value: () => ({ intent: state.intent, targets: state.targets }) };
}

// What people see on their device when Intune installs an application
// (win32LobAppNotification), with the key of each label.
const NOTIFY_KEYS = { showAll: "notify.show_all", showReboot: "notify.show_reboot", hideAll: "notify.hide_all" };

function notifySelect(value) {
  return select(Object.entries(NOTIFY_KEYS).map(([key, label]) => [key, t(label)]), value in NOTIFY_KEYS ? value : "showAll");
}

const NOTIFY_HINTS = { showAll: "notify.show_all_hint", showReboot: "notify.show_reboot_hint", hideAll: "notify.hide_all_hint" };

// The setting, what the chosen option shows on the device, then when it is used.
function notifyField(control) {
  const id = `notify-${++groupEditors}`;
  return [...field(t("notify.label"), control), explained(control, (value) => t(NOTIFY_HINTS[value]), id), h("p", { class: "hint ge-after" }, t("notify.hint"))];
}

const WHERE_HINTS = { pilot: "rollout.to_pilot_hint", prod: "rollout.to_prod_hint", none: "rollout.to_none_hint" };

// "Assign ... to": each choice says who receives the application when it is published.
function whereField(control, isUpdate, hasPilot) {
  const id = `where-${++groupEditors}`;
  const textFor = (value) => {
    const parts = [t(WHERE_HINTS[value])];
    // Only an update has a previous version to copy the assignments from.
    if (isUpdate && value === "prod") parts.push(t("rollout.to_prod_previous"));
    if (isUpdate && !hasPilot) parts.push(t("rollout.no_pilot_hint"));
    return parts.join(" ");
  };
  return [...field(t(isUpdate ? "rollout.where" : "rollout.where_new"), control), explained(control, textFor, id)];
}

// ---------------------------------------------------------------- advanced settings

const RESTART_KEYS = {
  basedOnReturnCode: "advanced.restart_return_code",
  allow: "advanced.restart_allow",
  suppress: "advanced.restart_suppress",
  force: "advanced.restart_force",
};
// The numeric fields: key in the form and label (which gives the unit).
const ADVANCED_NUMBERS = [
  ["disk_space_mb", "advanced.disk_space"],
  ["memory_mb", "advanced.memory"],
  ["processors", "advanced.processors"],
  ["cpu_speed_mhz", "advanced.cpu_speed"],
  ["max_run_time", "advanced.max_run_time"],
];

// "1607" -> "Windows 10 1607", "Windows11_23H2" -> "Windows 11 23H2". Intune writes 20H2 as "2H20".
function windowsLabel(value) {
  if (value === "2H20") return "Windows 10 20H2";
  const named = /^Windows(\d+)_(.+)$/.exec(value);
  if (named) return `Windows ${named[1]} ${named[2]}`;
  return /^\d{4}$|^\d\dH\d$/.test(value) ? `Windows 10 ${value}` : value;
}

// Collapsed block of the review screen. Filled with the defaults of the tool for a new
// application, with what Intune holds for an update: what is left alone is not sent.
function advancedEditor(advanced, isUpdate) {
  const numbers = {};
  for (const [key] of ADVANCED_NUMBERS) {
    numbers[key] = input(advanced[key] ?? "", { inputMode: "numeric", autocomplete: "off", placeholder: key === "max_run_time" ? "60" : "" });
  }
  const releases = (config.windows_releases || []).map((value) => [value, windowsLabel(value)]);
  if (advanced.windows_release && !releases.some(([value]) => value === advanced.windows_release)) {
    releases.push([advanced.windows_release, windowsLabel(advanced.windows_release)]);
  }
  if (!advanced.windows_release) releases.unshift(["", t("advanced.windows_unset")]);
  const windows = select(releases, advanced.windows_release || "");
  const restart = select(Object.entries(RESTART_KEYS).map(([key, label]) => [key, t(label)]), advanced.restart_behavior);
  const allowUninstall = h("input", { type: "checkbox", checked: !!advanced.allow_uninstall });
  const body = h(
    "div",
    { id: "advanced-settings", hidden: true },
    h("h3", { class: "sub" }, t("advanced.requirements")),
    h("p", { class: "hint" }, t("advanced.requirements_hint")),
    h("div", { class: "two" }, h("div", {}, field(t("advanced.disk_space"), numbers.disk_space_mb)), h("div", {}, field(t("advanced.memory"), numbers.memory_mb))),
    h("div", { class: "two" }, h("div", {}, field(t("advanced.processors"), numbers.processors)), h("div", {}, field(t("advanced.cpu_speed"), numbers.cpu_speed_mhz))),
    field(t("advanced.windows"), windows),
    h("h3", { class: "sub" }, t("advanced.install")),
    field(t("advanced.restart"), restart, t("advanced.restart_hint")),
    field(t("advanced.max_run_time"), numbers.max_run_time, t("advanced.max_run_time_hint")),
    h("label", { class: "check" }, allowUninstall, h("span", {}, t("advanced.allow_uninstall"))),
  );
  const toggle = h("button", { type: "button", class: "ghost", "aria-expanded": "false", "aria-controls": "advanced-settings" }, t("advanced.show"));
  toggle.addEventListener("click", () => {
    body.hidden = !body.hidden;
    toggle.setAttribute("aria-expanded", String(!body.hidden));
    toggle.textContent = t(body.hidden ? "advanced.show" : "advanced.hide");
  });
  return {
    node: h("div", { class: "panel" }, h("h2", {}, t("advanced.title")), h("p", { class: "muted" }, t(isUpdate ? "advanced.intro_update" : "advanced.intro_new")), toggle, body),
    // The label of the first numeric field that is not a whole number above zero, if any.
    invalid: () => {
      const bad = ADVANCED_NUMBERS.find(([key]) => !/^(\d{0,9}[1-9]\d{0,9})?$/.test(numbers[key].value.trim()) || numbers[key].value.trim().length > 10);
      if (bad && body.hidden) toggle.click();
      return bad ? t(bad[1]) : null;
    },
    value: () => ({
      ...Object.fromEntries(ADVANCED_NUMBERS.map(([key]) => [key, numbers[key].value.trim()])),
      windows_release: windows.value,
      restart_behavior: restart.value,
      allow_uninstall: allowUninstall.checked,
    }),
  };
}

// The same settings, read-only, on the application page.
function advancedFacts(advanced) {
  const rows = ADVANCED_NUMBERS.map(([key, label]) => [t(label), advanced[key] ? String(advanced[key]) : "—"]);
  rows.splice(4, 0, [t("advanced.windows"), advanced.windows_release ? windowsLabel(advanced.windows_release) : "—"], [t("advanced.restart"), t(RESTART_KEYS[advanced.restart_behavior] || "advanced.restart_return_code")]);
  rows.push([t("advanced.allow_uninstall"), t(advanced.allow_uninstall ? "advanced.yes" : "advanced.no")]);
  return h("details", { class: "more" }, h("summary", { class: "muted" }, t("advanced.title")), h("dl", { class: "facts" }, rows.flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, value)])));
}

// ---------------------------------------------------------------- app detail

async function showApp(appId) {
  show(h("p", { class: "muted" }, t("apps.loading")));
  let detail;
  try {
    detail = await api("GET", `/api/apps/${appId}`);
  } catch (error) {
    show(errorBox(error), button(t("action.back"), showApps, "ghost"));
    return;
  }
  const { app } = detail;
  const stageClass = detail.stage === "production" ? "ok" : detail.stage === "pilot" ? "pilot" : "";
  const message = h("div", {});
  const pilot = stageEditor(t("rollout.pilot"), t("rollout.pilot_hint"), detail.rollout.pilot);
  const prod = stageEditor(t("rollout.prod"), t("rollout.prod_hint"), detail.rollout.prod);
  const notifications = notifySelect(detail.rollout.notifications);
  const assignmentText = (item) => {
    const label = { allDevices: t("target.all_devices"), allUsers: t("target.all_users"), exclusion: t("target.exclusion") }[item.type];
    const who = item.id ? `${label ? `${label}: ` : ""}${item.name ? `${item.name} (${item.id})` : item.id}` : label || t("target.other");
    // Said only when it differs from what Intune does by default.
    const quiet = item.notifications in NOTIFY_KEYS && item.notifications !== "showAll" ? ` — ${t(NOTIFY_KEYS[item.notifications])}` : "";
    return `${t(`intent.${item.intent}`)} — ${who}${quiet}`;
  };
  show(
    h(
      "div",
      { class: "head" },
      h("h1", {}, app.name, " ", h("span", { class: `badge ${stageClass}` }, t(`stage.${detail.stage}`))),
      button(t("action.back"), showApps, "ghost"),
      button(t("action.update"), () => showWizard(appId)),
    ),
    detail.newer.length
      ? h("div", { class: "notice" }, t("app.superseded_by", { name: detail.newer.map((item) => `${item.name} ${item.version}`).join(", ") }))
      : null,
    h(
      "div",
      { class: "panel" },
      h(
        "dl",
        { class: "facts" },
        h("dt", {}, t("apps.version")), h("dd", {}, app.version || "—"),
        h("dt", {}, t("apps.publisher")), h("dd", {}, app.publisher),
        h("dt", {}, t("app.file")), h("dd", { class: "mono" }, app.file),
        h("dt", {}, t("form.install")), h("dd", { class: "mono" }, app.install_command),
        h("dt", {}, t("form.uninstall")), h("dd", { class: "mono" }, app.uninstall_command),
        h("dt", {}, t("form.run_as")), h("dd", {}, t(app.run_as === "user" ? "run_as.user" : "run_as.system")),
        h("dt", {}, t("app.previous")),
        h("dd", {}, detail.older.length ? detail.older.map((item) => `${item.name} ${item.version}`).join(", ") : t("app.none")),
        h("dt", {}, t("app.assignments")),
        h("dd", {}, detail.assignments.length ? h("ul", {}, detail.assignments.map((item) => h("li", {}, assignmentText(item)))) : t("app.none")),
      ),
      advancedFacts(app.advanced),
    ),
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("rollout.title")),
      h("p", { class: "muted" }, t("rollout.text")),
      pilot.node,
      prod.node,
      notifyField(notifications),
      message,
      h(
        "div",
        { class: "row end" },
        button(t("action.save_groups"), async (event) => {
          try {
            await busy(event.target, () => api("PUT", `/api/apps/${appId}/rollout`, { name: app.name, pilot: pilot.value(), prod: prod.value(), notifications: notifications.value }));
            showApp(appId);
          } catch (error) {
            message.replaceChildren(errorBox(error));
          }
        }, "ghost"),
        detail.can_promote
          ? button(t("action.promote"), async (event) => {
              if (!window.confirm(t("rollout.confirm_promote", { name: app.name }))) return;
              try {
                await busy(event.target, () => api("POST", `/api/apps/${appId}/promote`));
                showApp(appId);
              } catch (error) {
                message.replaceChildren(errorBox(error));
              }
            })
          : null,
      ),
      notes(t("rollout.save_hint"), detail.can_promote ? t("rollout.promote_hint") : null),
    ),
    uninstallPanel(detail, appId),
  );
}

// Removing an Intune-managed application from some devices: Intune itself does it,
// through an "uninstall" assignment of the application.
function uninstallPanel(detail, appId) {
  const targets = stageEditor(t("removal.app_targets"), t("removal.app_targets_hint"), { targets: [] }, { noIntent: true });
  const message = h("div", {});
  const active = detail.assignments.some((item) => item.intent === "uninstall");
  const send = async (list, control) => {
    try {
      await busy(control, () => api("POST", `/api/apps/${appId}/uninstall`, { targets: list }));
      showApp(appId);
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  return h(
    "div",
    { class: "panel" },
    h("h2", {}, t("removal.app_title")),
    h("p", { class: "muted" }, t("removal.app_text")),
    h("div", { class: "notice" }, t("removal.warning")),
    targets.node,
    message,
    h(
      "div",
      { class: "row end" },
      active ? button(t("removal.app_stop"), (event) => send([], event.target), "ghost") : null,
      button(t("removal.app_start"), (event) => {
        const list = targets.value().targets;
        if (!list.length) {
          message.replaceChildren(h("div", { class: "notice error" }, t("error.removal_no_target")));
          return;
        }
        if (window.confirm(t("removal.app_confirm", { name: detail.app.name }))) send(list, event.target);
      }, "danger"),
    ),
    active ? notes(t("removal.app_stop_hint")) : null,
  );
}

// ---------------------------------------------------------------- wizard

function showWizard(appId) {
  const url = h("input", { type: "url", placeholder: "https://…", autofocus: true });
  const file = h("input", { type: "file", accept: ".msi,.exe" });
  const message = h("div", {});
  const start = async (event) => {
    message.replaceChildren();
    try {
      const job = await busy(event.target, () => {
        if (file.files.length) {
          const data = new FormData();
          data.append("file", file.files[0]);
          data.append("app_id", appId || "");
          return api("POST", "/api/analyze/upload", data);
        }
        return api("POST", "/api/analyze", { url: url.value, app_id: appId });
      });
      followJob(job.id, appId);
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  show(
    h("div", { class: "head" }, h("h1", {}, t(appId ? "wizard.update_title" : "wizard.new_title")), button(t("action.cancel"), appId ? () => showApp(appId) : showApps, "ghost")),
    h(
      "div",
      { class: "panel" },
      field(t("wizard.link"), url, t("wizard.link_hint")),
      field(t("wizard.file"), file, t("wizard.file_hint", { size: bytes(config.max_download_mb * 1024 * 1024) })),
      message,
      h("div", { class: "row end" }, button(t("action.analyze"), start)),
    ),
  );
}

function progressPanel(job) {
  const ratio = job.total_bytes ? job.done_bytes / job.total_bytes : null;
  return h(
    "div",
    { class: "panel", "aria-live": "polite" },
    h("ol", { class: "steps" }, job.steps.map((step, index) => h("li", { class: index === job.steps.length - 1 ? "current" : "" }, t(step)))),
    ratio == null ? h("progress", {}) : h("progress", { value: ratio, max: 1 }),
    h("p", { class: "muted" }, job.total_bytes ? `${bytes(job.done_bytes)} / ${bytes(job.total_bytes)}` : job.done_bytes ? bytes(job.done_bytes) : t("wizard.working")),
  );
}

async function followJob(jobId, appId) {
  const title = h("div", { class: "head" }, h("h1", {}, t("wizard.progress_title")));
  for (;;) {
    let job;
    try {
      job = await api("GET", `/api/jobs/${jobId}`);
    } catch (error) {
      if (error.code !== "error.sign_in_required") show(errorBox(error), button(t("action.back"), showApps, "ghost"));
      return;
    }
    if (job.state === "ready") return showReview(job, appId);
    if (job.state === "done") return showDone(job);
    if (job.state === "error") {
      show(title, errorBox(job.error), button(t("action.back"), () => showWizard(appId), "ghost"));
      return;
    }
    show(title, progressPanel(job));
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function detectionEditor(detection, analysis, isUpdate) {
  const state = { ...detection };
  const body = h("div", {});
  const types = [];
  if (analysis.kind === "msi") types.push(["msi", t("detection.msi")]);
  types.push(["file", t("detection.file")], ["registry", t("detection.registry")]);
  if (isUpdate) types.push(["keep", t("detection.keep")]);
  const controls = {};
  const draw = () => {
    const text = (key, label, attrs = {}) => {
      controls[key] = input(state[key], { class: "mono", ...attrs, oninput: (event) => { state[key] = event.target.value; } });
      return field(label, controls[key]);
    };
    const operator = (choices) =>
      field(
        t("detection.operator"),
        select(choices.map((key) => [key, t(`detection.${key}`)]), choices.includes(state.operator) ? state.operator : choices[0], {
          onchange: (event) => { state.operator = event.target.value; draw(); },
        }),
      );
    const check32 = h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!state.check32, onchange: (event) => { state.check32 = event.target.checked; } }), t("detection.check32"));
    let nodes = [];
    if (state.type === "msi") {
      state.product_code ??= analysis.msi?.product_code || "";
      nodes = [text("product_code", t("detection.product_code"))];
    } else if (state.type === "file") {
      if (!["exists", "version_ge"].includes(state.operator)) state.operator = "version_ge";
      nodes = [
        h("div", { class: "two" }, h("div", {}, text("path", t("detection.path"), { placeholder: "C:\\Program Files\\App" })), h("div", {}, text("name", t("detection.file_name"), { placeholder: "app.exe" }))),
        h("div", { class: "two" }, h("div", {}, operator(["version_ge", "exists"])), h("div", {}, state.operator === "exists" ? null : text("value", t("detection.value")))),
        check32,
      ];
    } else if (state.type === "registry") {
      if (!["exists", "version_ge", "string_eq"].includes(state.operator)) state.operator = "version_ge";
      nodes = [
        text("key", t("detection.key"), { placeholder: "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\…" }),
        h("div", { class: "two" }, h("div", {}, text("value_name", t("detection.value_name"), { placeholder: "DisplayVersion" })), h("div", {}, operator(["version_ge", "string_eq", "exists"]))),
        state.operator === "exists" ? null : text("value", t("detection.value")),
        check32,
      ];
    } else {
      nodes = [h("p", { class: "hint" }, t("detection.keep_hint"))];
    }
    body.replaceChildren(...nodes.flat().filter(Boolean));
  };
  const type = select(types, state.type, {
    onchange: (event) => {
      state.type = event.target.value;
      if (state.value == null) state.value = analysis.version;
      draw();
    },
  });
  draw();
  return { node: h("div", {}, field(t("detection.type"), type), body), value: () => ({ ...state }) };
}

// Set by "Replace with a newer version" in the inventory, read once by the review screen.
let pendingReplace = null;

// Optional block of the review screen: only install where an older version is found,
// and if needed remove that older version first.
function replaceEditor(seed, version) {
  const state = { names: [...(seed?.names || [])] };
  const enabled = h("input", { type: "checkbox", checked: !!seed });
  const chips = h("div", { class: "chips" });
  const found = h("div", { class: "results" });
  const search = h("input", { type: "search", placeholder: t("removal.add_name") });
  const below = input(version || "", { placeholder: "24.0" });
  const mode = select([["upgrade", t("replace.upgrade")], ["reinstall", t("replace.reinstall")]], "upgrade");
  const modeHint = h("p", { class: "hint" });
  const command = input("", { class: "mono", placeholder: "{UninstallString} /S" });
  const commandBlock = h("div", {}, field(t("removal.command"), command, t("replace.command_hint")));
  const body = h("div", {});
  const drawNames = () => {
    chips.replaceChildren(
      ...(state.names.length
        ? state.names.map((name, index) => h("span", { class: "chip" }, name, h("button", { type: "button", "aria-label": t("action.remove"), onclick: () => { state.names.splice(index, 1); drawNames(); } }, "×")))
        : [h("span", { class: "muted" }, t("replace.no_name"))]),
    );
  };
  const draw = () => {
    body.hidden = !enabled.checked;
    modeHint.textContent = t(`replace.${mode.value}_hint`);
    commandBlock.hidden = mode.value !== "reinstall";
  };
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!search.value.trim()) {
        found.replaceChildren();
        return;
      }
      try {
        const result = await api("GET", `/api/inventory/apps?q=${encodeURIComponent(search.value)}`);
        const items = result.items.filter((item) => !state.names.some((name) => name.toLowerCase() === item.name.toLowerCase())).slice(0, 8);
        const add = (item) => {
          state.names.push(item.name);
          search.value = "";
          found.replaceChildren();
          drawNames();
        };
        found.replaceChildren(...(items.length ? items.map((item) => button(item.name, () => add(item), "linkish")) : [h("span", { class: "muted" }, t("apps.no_match"))]));
      } catch (error) {
        found.replaceChildren(errorBox(error));
      }
    }, 300);
  });
  enabled.addEventListener("change", draw);
  mode.addEventListener("change", draw);
  body.append(h("p", { class: "muted" }, t("replace.hint")), chips, search, found, ...field(t("replace.below"), below).filter(Boolean), ...field(t("replace.mode"), mode).filter(Boolean), modeHint, commandBlock);
  drawNames();
  draw();
  return {
    node: h("div", { class: "panel" }, h("h2", {}, t("replace.title")), h("label", { class: "check" }, enabled, h("span", {}, t("replace.enable"))), body),
    value: () => ({ enabled: enabled.checked, names: state.names, below: below.value.trim(), mode: mode.value, command: command.value }),
  };
}

function showReview(job, appId) {
  const { analysis, form, existing, older, rollout } = job.result;
  const isUpdate = !!existing;
  const replace = config.device_inventory ? replaceEditor(pendingReplace, form.version) : null;
  pendingReplace = null;
  const controls = {
    display_name: input(form.display_name),
    version: input(form.version),
    publisher: input(form.publisher),
    description: h("textarea", { value: form.description || "" }),
    install_command: input(form.install_command, { class: "mono" }),
    uninstall_command: input(form.uninstall_command, { class: "mono" }),
  };
  const detection = detectionEditor(form.detection, analysis, isUpdate);
  const advanced = advancedEditor(form.advanced, isUpdate);
  const message = h("div", {});
  const hasPilot = rollout.pilot.targets.length > 0;
  let extra = {};
  let options;

  if (isUpdate) {
    const mode = select([["supersede", t("mode.supersede")], ["inplace", t("mode.inplace")]], "supersede");
    const supersedence = select([["update", t("mode.type_update")], ["replace", t("mode.type_replace")]], "update");
    const target = select(
      [["pilot", t("rollout.to_pilot")], ["prod", t("rollout.to_prod")], ["none", t("rollout.to_none")]],
      hasPilot ? "pilot" : "prod",
    );
    if (!hasPilot) target.querySelector('option[value="pilot"]').disabled = true;
    const cleanup = h("input", { type: "checkbox", checked: true });
    const notifications = notifySelect(rollout.notifications);
    const runAs = select([["system", t("run_as.system")], ["user", t("run_as.user")]], form.run_as);
    const superseding = h(
      "div",
      {},
      field(t("mode.type"), supersedence, t("mode.type_hint")),
      whereField(target, true, hasPilot),
      notifyField(notifications),
      older.length
        ? h("label", { class: "check" }, cleanup, h("span", {}, t("mode.cleanup", { names: older.map((item) => `${item.name} ${item.version}`).join(", ") })))
        : null,
    );
    const inplace = h("p", { class: "hint", hidden: true }, t("mode.inplace_hint"));
    mode.addEventListener("change", () => {
      superseding.hidden = mode.value !== "supersede";
      inplace.hidden = mode.value !== "inplace";
    });
    options = h("div", { class: "panel" }, h("h2", {}, t("mode.title")), field(t("mode.label"), mode), superseding, inplace, field(t("form.run_as"), runAs, t("form.run_as_update_hint")));
    extra = () => ({ mode: mode.value, supersedence_type: supersedence.value, rollout: target.value, cleanup_old: cleanup.checked, run_as: runAs.value, notifications: notifications.value });
  } else {
    const architecture = select([["any", t("arch.any")], ["x64", "x64"], ["x86", "x86"], ["arm64", "ARM64"]], form.architecture);
    const runAs = select([["system", t("run_as.system")], ["user", t("run_as.user")]], form.run_as);
    const pilot = stageEditor(t("rollout.pilot"), t("rollout.pilot_hint"), rollout.pilot);
    const prod = stageEditor(t("rollout.prod"), t("rollout.prod_hint"), rollout.prod);
    const target = select([["none", t("rollout.to_none")], ["pilot", t("rollout.to_pilot")], ["prod", t("rollout.to_prod")]], "none");
    const notifications = notifySelect(rollout.notifications);
    options = h(
      "div",
      { class: "panel" },
      h("h2", {}, t("rollout.title")),
      h("p", { class: "muted" }, t("rollout.text_new")),
      h("div", { class: "two" }, h("div", {}, field(t("form.architecture"), architecture)), h("div", {}, field(t("form.run_as"), runAs))),
      pilot.node,
      prod.node,
      notifyField(notifications),
      whereField(target, false, true),
    );
    extra = () => ({ mode: "new", architecture: architecture.value, run_as: runAs.value, pilot: pilot.value(), prod: prod.value(), rollout: target.value, notifications: notifications.value });
  }

  const publish = async (event) => {
    message.replaceChildren();
    const body = { ...extra(), detection: detection.value(), advanced: advanced.value() };
    for (const [key, control] of Object.entries(controls)) body[key] = control.value;
    if (replace) body.replace = replace.value();
    const badNumber = advanced.invalid();
    if (badNumber) {
      message.replaceChildren(h("div", { class: "notice error", role: "alert" }, `${t("error.advanced_number")} — ${badNumber}`));
      return;
    }
    if (body.rollout === "pilot" && !isUpdate && !body.pilot.targets.length) {
      message.replaceChildren(h("div", { class: "notice error" }, t("error.no_pilot_groups")));
      return;
    }
    try {
      await busy(event.target, () => api("POST", `/api/jobs/${job.id}/publish`, body));
      followJob(job.id, appId);
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  const cancel = async () => {
    try {
      await api("DELETE", `/api/jobs/${job.id}`);
    } catch {
      /* already gone */
    }
    if (appId) showApp(appId);
    else showApps();
  };

  show(
    h("div", { class: "head" }, h("h1", {}, t("review.title")), button(t("action.cancel"), cancel, "ghost")),
    job.error ? errorBox(job.error) : null,
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("review.detected")),
      h(
        "dl",
        { class: "facts" },
        h("dt", {}, t("app.file")), h("dd", { class: "mono" }, `${analysis.file_name} (${bytes(analysis.size)})`),
        h("dt", {}, t("review.type")), h("dd", {}, analysis.installer_label || t("review.unknown_type")),
        h("dt", {}, "SHA-256"), h("dd", { class: "mono" }, analysis.sha256),
        isUpdate ? [h("dt", {}, t("review.current")), h("dd", {}, `${existing.name} — ${existing.version || "?"}`)] : null,
      ),
    ),
    form.warnings.length
      ? h("div", { class: "notice" }, t("review.check"), h("ul", {}, form.warnings.map((key) => h("li", {}, t(key)))))
      : null,
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("review.settings")),
      h("div", { class: "two" }, h("div", {}, field(t("apps.name"), controls.display_name)), h("div", {}, field(t("apps.version"), controls.version))),
      field(t("apps.publisher"), controls.publisher),
      field(t("form.description"), controls.description),
      field(t("form.install"), controls.install_command, analysis.kind === "exe" && !analysis.installer_type ? t("form.install_unknown") : null),
      field(t("form.uninstall"), controls.uninstall_command, analysis.uninstall_hint ? t("form.uninstall_hint", { example: analysis.uninstall_hint }) : null),
    ),
    h("div", { class: "panel" }, h("h2", {}, t("detection.title")), h("p", { class: "muted" }, t("detection.text")), detection.node),
    options,
    replace ? replace.node : null,
    advanced.node,
    message,
    h(
      "div",
      { class: "row end" },
      h("a", { class: "btn ghost", href: `/api/jobs/${job.id}/intunewin`, download: "" }, t("action.download_package")),
      button(t("action.publish"), publish),
    ),
  );
}

function showDone(job) {
  const { outcome } = job.result;
  show(
    h("div", { class: "head" }, h("h1", {}, t("done.title"))),
    h("div", { class: "notice ok", role: "status" }, t(`done.${outcome.mode}`, { name: outcome.name })),
    outcome.removed?.length ? h("div", { class: "notice" }, t("done.removed", { names: outcome.removed.map((item) => `${item.name} ${item.version}`).join(", ") })) : null,
    outcome.cleanup_failed?.length
      ? h("div", { class: "notice error" }, t("done.cleanup_failed", { names: outcome.cleanup_failed.map((item) => `${item.name} ${item.version}`).join(", ") }), " ", outcome.cleanup_failed[0].error)
      : null,
    h("p", { class: "muted" }, t("done.delay")),
    h("div", { class: "row" }, button(t("done.open"), () => showApp(outcome.app_id)), button(t("action.back_to_list"), showApps, "ghost")),
  );
}

// ---------------------------------------------------------------- device inventory

function when(value) {
  if (!value) return "—";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(document.documentElement.lang, { dateStyle: "short", timeStyle: "short" });
}

function table(headers, rows) {
  return h(
    "div",
    { class: "table-wrap" },
    h("table", {}, h("thead", {}, h("tr", {}, headers.map((label) => h("th", {}, label)))), h("tbody", {}, rows)),
  );
}

function pager(result, go) {
  if (result.pages <= 1) return null;
  return h(
    "div",
    { class: "row end pager" },
    button(t("inventory.previous"), () => go(result.page - 1), "ghost small"),
    h("span", { class: "muted" }, t("inventory.page", { page: result.page, pages: result.pages })),
    button(t("inventory.next"), () => go(result.page + 1), "ghost small"),
  );
}

function deviceRows(devices) {
  return devices.map((device) =>
    h(
      "tr",
      {},
      h("td", {}, button(device.name || device.id, () => showDevice(device.id), "linkish")),
      h("td", {}, device.user || "—"),
      h("td", {}, device.model || "—"),
      h("td", {}, device.os_version || "—"),
      h("td", {}, when(device.last_sync)),
    ),
  );
}

function deviceHeaders() {
  return [t("inventory.device"), t("inventory.user"), t("inventory.model"), t("inventory.os"), t("inventory.last_sync")];
}

// Software found on the devices (tab "software") or the devices themselves (tab "devices").
function showInventory(tab = "software", query = "") {
  drawNav("inventory");
  const bySoftware = tab === "software";
  const search = h("input", { type: "search", class: "grow", value: query, placeholder: t(bySoftware ? "inventory.search_software" : "inventory.search_device") });
  const body = h("div", { class: "panel" }, h("p", { class: "muted" }, t("apps.loading")));
  const note = h("p", { class: "hint" });
  const switcher = (key, label) =>
    h("button", { type: "button", class: `tab ${tab === key ? "current" : ""}`, "aria-current": tab === key ? "page" : null, onclick: () => showInventory(key) }, label);
  let request = 0;

  const load = async (page = 1, refresh = false) => {
    const mine = ++request;
    const params = new URLSearchParams({ q: search.value, page });
    if (refresh) params.set("refresh", "true");
    let result;
    try {
      result = await api("GET", `/api/inventory/${bySoftware ? "apps" : "devices"}?${params}`);
    } catch (error) {
      if (mine === request) body.replaceChildren(errorBox(error));
      return;
    }
    if (mine !== request) return; // a newer search answered first
    note.textContent = t("inventory.note", { time: when(result.fetched_at) });
    if (!result.total) {
      body.replaceChildren(h("p", { class: "empty muted" }, t(search.value ? "apps.no_match" : bySoftware ? "inventory.no_software" : "inventory.no_device")));
      return;
    }
    const rows = bySoftware
      ? result.items.map((item) =>
          h(
            "tr",
            {},
            h("td", {}, button(item.name, () => showSoftware(item, search.value), "linkish")),
            h("td", {}, item.publisher || "—"),
            h("td", {}, item.versions.length === 1 ? item.versions[0].version || "—" : t("inventory.several", { count: item.versions.length })),
            h("td", {}, String(item.installs)),
          ),
        )
      : deviceRows(result.items);
    const headers = bySoftware ? [t("apps.name"), t("apps.publisher"), t("apps.version"), t("inventory.installs")] : deviceHeaders();
    body.replaceChildren(table(headers, rows), pager(result, (next) => load(next)) || "");
  };

  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(1), 250);
  });
  show(
    h("div", { class: "head" }, h("h1", {}, t("inventory.title")), h("div", { class: "tabs" }, switcher("software", t("inventory.by_software")), switcher("devices", t("inventory.by_device")))),
    h("div", { class: "row" }, search, button(t("inventory.refresh"), (event) => busy(event.target, () => load(1, true)), "ghost")),
    note,
    body,
  );
  return load(1);
}

// One piece of software: its versions, and on request the devices that have each version.
function showSoftware(item, query) {
  drawNav("inventory");
  const devices = h("div", {});
  const open = async (entry, control) => {
    devices.replaceChildren(h("div", { class: "panel" }, h("p", { class: "muted" }, t("apps.loading"))));
    try {
      const result = await busy(control, () => api("GET", `/api/inventory/apps/${encodeURIComponent(entry.id)}/devices`));
      devices.replaceChildren(
        h(
          "div",
          { class: "panel" },
          h("h2", {}, t("inventory.devices_with", { version: entry.version || "—" })),
          result.devices.length ? table(deviceHeaders(), deviceRows(result.devices)) : h("p", { class: "muted" }, t("inventory.no_device")),
        ),
      );
    } catch (error) {
      devices.replaceChildren(errorBox(error));
    }
  };
  show(
    h(
      "div",
      { class: "head" },
      h("h1", {}, item.name),
      button(t("action.back"), () => showInventory("software", query), "ghost"),
      button(t("replace.start"), () => {
        pendingReplace = { names: [item.name] };
        showWizard(null);
      }, "ghost"),
      item.protected ? null : button(t("removal.start"), () => showRemovalForm({ names: [item.name], known: item.versions.map((entry) => entry.version) }), "danger"),
    ),
    item.protected ? h("div", { class: "notice" }, t("removal.protected_notice")) : null,
    h(
      "div",
      { class: "panel" },
      h("dl", { class: "facts" }, h("dt", {}, t("apps.publisher")), h("dd", {}, item.publisher || "—"), h("dt", {}, t("inventory.installs")), h("dd", {}, String(item.installs))),
    ),
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("inventory.versions")),
      table(
        [t("apps.version"), t("inventory.devices"), ""],
        item.versions.map((entry) =>
          h("tr", {}, h("td", {}, entry.version || "—"), h("td", {}, String(entry.devices)), h("td", { class: "actions" }, button(t("inventory.show_devices"), (event) => open(entry, event.target), "small"))),
        ),
      ),
    ),
    devices,
  );
  if (item.versions.length === 1) open(item.versions[0], null);
}

// One device and everything Intune found on it.
async function showDevice(deviceId) {
  drawNav("inventory");
  show(h("p", { class: "muted" }, t("apps.loading")));
  let detail;
  try {
    detail = await api("GET", `/api/inventory/devices/${encodeURIComponent(deviceId)}`);
  } catch (error) {
    show(errorBox(error), button(t("action.back"), () => showInventory("devices"), "ghost"));
    return;
  }
  const { device, apps } = detail;
  show(
    h("div", { class: "head" }, h("h1", {}, device.name), button(t("action.back"), () => showInventory("devices"), "ghost")),
    h(
      "div",
      { class: "panel" },
      h(
        "dl",
        { class: "facts" },
        h("dt", {}, t("inventory.user")), h("dd", {}, device.user || "—"),
        h("dt", {}, t("inventory.model")), h("dd", {}, device.model || "—"),
        h("dt", {}, t("inventory.os")), h("dd", {}, device.os_version || "—"),
        h("dt", {}, t("inventory.last_sync")), h("dd", {}, when(device.last_sync)),
      ),
    ),
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("inventory.software_on", { count: apps.length })),
      apps.length
        ? table(
            [t("apps.name"), t("apps.version"), t("apps.publisher"), ""],
            apps.map((app) =>
              h(
                "tr",
                {},
                h("td", {}, button(app.name, () => showInventory("software", app.name), "linkish")),
                h("td", {}, app.version || "—"),
                h("td", {}, app.publisher || "—"),
                h("td", { class: "actions" }, config.remediations && !app.protected ? button(t("removal.here"), () => showOnDemandForm(device, app), "small danger") : null),
              ),
            ),
          )
        : h("p", { class: "muted" }, t("inventory.no_software")),
    ),
  );
}

// ---------------------------------------------------------------- removals

const COMMAND_PRESETS = [
  ["", ""],
  ["{UninstallString} /S", "NSIS"],
  ["{UninstallString} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART", "Inno Setup"],
  ["{UninstallString} /quiet /norestart", "/quiet"],
];

// Runs a background task to its end, showing its steps.
async function followTask(jobId, title, onDone, onBack) {
  const head = h("div", { class: "head" }, h("h1", {}, title));
  for (;;) {
    let job;
    try {
      job = await api("GET", `/api/jobs/${jobId}`);
    } catch (error) {
      if (error.code !== "error.sign_in_required") show(errorBox(error), button(t("action.back"), onBack, "ghost"));
      return;
    }
    if (job.state === "done") return onDone(job);
    if (job.state === "error") {
      show(head, errorBox(job.error), button(t("action.back"), onBack, "ghost"));
      return;
    }
    show(head, progressPanel(job));
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function describeVersions(rule) {
  if (rule.mode === "list") return t("removal.versions_list_value", { versions: rule.values.join(", ") });
  if (rule.mode === "below") return t("removal.versions_below_value", { version: rule.value });
  return t("removal.versions_any");
}

async function showRemovals() {
  drawNav("removals");
  const body = h("div", { class: "panel" }, h("p", { class: "muted" }, t("apps.loading")));
  show(h("div", { class: "head" }, h("h1", {}, t("removal.title"))), h("p", { class: "muted" }, t("removal.intro")), body);
  let rules;
  try {
    rules = (await api("GET", "/api/removals")).rules;
  } catch (error) {
    body.replaceChildren(errorBox(error));
    return;
  }
  if (!rules.length) {
    body.replaceChildren(h("p", { class: "empty muted" }, t("removal.none")), h("div", { class: "row end" }, button(t("removal.open_inventory"), () => showInventory("software"))));
    return;
  }
  body.replaceChildren(
    table(
      [t("removal.software"), t("removal.kind"), t("removal.duration"), t("removal.targets"), t("removal.created")],
      rules.map((rule) =>
        h(
          "tr",
          {},
          h("td", {}, button(rule.config.names.join(", "), () => showRemoval(rule.id), "linkish")),
          h("td", {}, t(`removal.kind_${rule.config.kind}`)),
          h("td", {}, t(rule.mechanism === "remediation" ? "removal.on_demand" : rule.permanent ? "removal.permanent" : "removal.once")),
          h("td", {}, rule.mechanism === "remediation" ? rule.devices.map((device) => device.name).join(", ") : rule.targets.length ? rule.targets.map(targetLabel).join(", ") : t("removal.no_target")),
          h("td", {}, when(rule.created_at)),
        ),
      ),
    ),
  );
}

// The form used to create a removal rule, or to change what an existing one removes.
function showRemovalForm(seed, rule = null) {
  drawNav("removals");
  const editing = !!rule;
  const looksLikeStore = /^[A-Za-z0-9]+(\.[A-Za-z0-9]+)+$/.test(seed.names[0]);
  const initial = rule ? rule.config : { names: seed.names, kind: looksLikeStore ? "store" : "machine", versions: { mode: "any" }, command: "" };
  const state = { names: [...initial.names], known: [...(seed.known || [])], versions: JSON.parse(JSON.stringify(initial.versions)) };
  const back = () => (editing ? showRemoval(rule.id) : showInventory("software", state.names[0]));

  const chips = h("div", { class: "chips" });
  const found = h("div", { class: "results" });
  const search = h("input", { type: "search", placeholder: t("removal.add_name") });
  const kind = select([["machine", t("removal.kind_machine")], ["user", t("removal.kind_user")], ["store", t("removal.kind_store")]], initial.kind);
  const kindHint = h("p", { class: "hint" });
  const versionMode = select([["any", t("removal.versions_any")], ["list", t("removal.versions_list")], ["below", t("removal.versions_below")]], state.versions.mode);
  const versionBody = h("div", {});
  const command = input(initial.command || "", { class: "mono", placeholder: "{UninstallString} /S" });
  const preset = select(COMMAND_PRESETS.map(([value, label]) => [value, label ? `${label} — ${value}` : t("removal.preset")]), "");
  const commandBlock = h("div", {}, field(t("removal.command"), command, t("removal.command_hint")), field(t("removal.preset_label"), preset));
  const targets = stageEditor(t("removal.targets"), t("removal.targets_hint"), { targets: [] }, { noIntent: true });
  const duration = select([["once", t("removal.once_long")], ["permanent", t("removal.permanent_long")]], "once");
  const summary = h("div", {});
  const confirm = input("", { autocomplete: "off" });
  const message = h("div", {});
  const publish = button(t(editing ? "removal.save" : "removal.publish"), null, "danger");

  const config = () => {
    const versions = { mode: versionMode.value };
    if (versions.mode === "list") versions.values = state.versions.values || [];
    if (versions.mode === "below") versions.value = state.versions.value || "";
    return { names: state.names, kind: kind.value, versions, command: command.value };
  };

  let request = 0;
  const preview = async () => {
    const mine = ++request;
    let result;
    try {
      result = await api("POST", "/api/removals/preview", { config: config() });
    } catch (error) {
      if (mine === request) summary.replaceChildren(errorBox(error));
      return;
    }
    if (mine !== request) return;
    confirm.placeholder = result.confirm;
    publish.disabled = result.protected.length > 0;
    summary.replaceChildren(
      ...[
        result.protected.length ? h("div", { class: "notice error" }, t("error.removal_protected"), " ", result.protected.join(", ")) : null,
        h("div", { class: "notice" }, t("removal.preview", { count: result.installs })),
        result.matches.length
          ? table([t("removal.software"), t("apps.version"), t("inventory.devices")], result.matches.map((row) => h("tr", {}, h("td", {}, row.name), h("td", {}, row.version || "—"), h("td", {}, String(row.devices)))))
          : null,
      ].filter(Boolean),
    );
  };
  let timer;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(preview, 300);
  };

  const drawNames = () => {
    chips.replaceChildren(
      ...state.names.map((name, index) =>
        h(
          "span",
          { class: "chip" },
          name,
          state.names.length > 1
            ? h("button", { type: "button", "aria-label": t("action.remove"), onclick: () => { state.names.splice(index, 1); drawNames(); changed(); } }, "×")
            : null,
        ),
      ),
    );
  };
  const drawKind = () => {
    kindHint.textContent = t(`removal.kind_${kind.value}_hint`);
    commandBlock.hidden = kind.value === "store";
  };
  const drawVersions = () => {
    const mode = versionMode.value;
    if (mode === "list") {
      const chosen = new Set(state.versions.values || []);
      const text = input([...chosen].join(", "), {
        placeholder: "1.2.3, 1.2.4",
        oninput: (event) => {
          state.versions.values = event.target.value.split(",").map((value) => value.trim()).filter(Boolean);
          changed();
        },
      });
      const boxes = state.known.filter(Boolean).map((version) =>
        h(
          "label",
          { class: "check" },
          h("input", {
            type: "checkbox",
            checked: chosen.has(version),
            onchange: (event) => {
              if (event.target.checked) chosen.add(version);
              else chosen.delete(version);
              state.versions.values = [...chosen];
              text.value = [...chosen].join(", ");
              changed();
            },
          }),
          version,
        ),
      );
      versionBody.replaceChildren(...boxes, ...field(t("removal.versions_list_label"), text).filter(Boolean));
    } else if (mode === "below") {
      const limit = input(state.versions.value || "", {
        placeholder: "24.0",
        oninput: (event) => {
          state.versions.value = event.target.value.trim();
          changed();
        },
      });
      versionBody.replaceChildren(...field(t("removal.versions_below_label"), limit).filter(Boolean));
    } else {
      versionBody.replaceChildren();
    }
  };

  let searchTimer;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      if (!search.value.trim()) {
        found.replaceChildren();
        return;
      }
      try {
        const result = await api("GET", `/api/inventory/apps?q=${encodeURIComponent(search.value)}`);
        const items = result.items.filter((item) => !item.protected && !state.names.some((name) => name.toLowerCase() === item.name.toLowerCase())).slice(0, 8);
        const add = (item) => {
          state.names.push(item.name);
          for (const entry of item.versions) if (!state.known.includes(entry.version)) state.known.push(entry.version);
          search.value = "";
          found.replaceChildren();
          drawNames();
          drawVersions();
          changed();
        };
        found.replaceChildren(...(items.length ? items.map((item) => button(item.name, () => add(item), "linkish")) : [h("span", { class: "muted" }, t("apps.no_match"))]));
      } catch (error) {
        found.replaceChildren(errorBox(error));
      }
    }, 300);
  });
  kind.addEventListener("change", () => {
    drawKind();
    changed();
  });
  versionMode.addEventListener("change", () => {
    drawVersions();
    changed();
  });
  command.addEventListener("input", changed);
  preset.addEventListener("change", () => {
    if (preset.value) command.value = preset.value;
    changed();
  });

  publish.addEventListener("click", async () => {
    message.replaceChildren();
    const body = { config: config(), confirm: confirm.value };
    if (!editing) {
      body.targets = targets.value().targets;
      body.permanent = duration.value === "permanent";
    }
    try {
      const job = await busy(publish, () => api(editing ? "PUT" : "POST", editing ? `/api/removals/${rule.id}` : "/api/removals", body));
      followTask(
        job.id,
        t("removal.progress"),
        (done) => showRemoval(done.result.removal.id),
        () => showRemovalForm({ names: state.names, known: state.known }, rule),
      );
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  });

  drawNames();
  drawKind();
  drawVersions();
  show(
    h("div", { class: "head" }, h("h1", {}, t(editing ? "removal.edit_title" : "removal.new_title")), button(t("action.cancel"), back, "ghost")),
    h("div", { class: "notice" }, t("removal.warning")),
    h("div", { class: "panel" }, h("h2", {}, t("removal.software")), h("p", { class: "muted" }, t("removal.exact_hint")), chips, search, found),
    h("div", { class: "panel" }, h("h2", {}, t("removal.versions")), field(t("removal.versions_label"), versionMode), versionBody),
    h("div", { class: "panel" }, h("h2", {}, t("removal.how")), field(t("removal.kind"), kind), kindHint, commandBlock),
    editing ? null : h("div", { class: "panel" }, h("h2", {}, t("removal.where")), targets.node, field(t("removal.duration"), duration)),
    h("div", { class: "panel" }, h("h2", {}, t("removal.confirm_title")), summary, field(t("removal.confirm_label"), confirm), message, h("div", { class: "row end" }, publish)),
  );
  preview();
}

// Removing one program from one device, now, through an Intune remediation script.
function showOnDemandForm(device, app) {
  drawNav("inventory");
  const looksLikeStore = /^[A-Za-z0-9]+(\.[A-Za-z0-9]+)+$/.test(app.name);
  const versions = select([["this", t("removal.this_version", { version: app.version || "—" })], ["any", t("removal.versions_any")]], app.version ? "this" : "any");
  const kind = select([["machine", t("removal.kind_machine")], ["user", t("removal.kind_user")], ["store", t("removal.kind_store")]], looksLikeStore ? "store" : "machine");
  const kindHint = h("p", { class: "hint" });
  const command = input("", { class: "mono", placeholder: "{UninstallString} /S" });
  const preset = select(COMMAND_PRESETS.map(([value, label]) => [value, label ? `${label} — ${value}` : t("removal.preset")]), "");
  const commandBlock = h("div", {}, field(t("removal.command"), command, t("removal.command_hint")), field(t("removal.preset_label"), preset));
  const confirm = input("", { autocomplete: "off", placeholder: app.name });
  const message = h("div", {});
  const drawKind = () => {
    kindHint.textContent = t(`removal.kind_${kind.value}_hint`);
    commandBlock.hidden = kind.value === "store";
  };
  kind.addEventListener("change", drawKind);
  preset.addEventListener("change", () => {
    if (preset.value) command.value = preset.value;
  });
  const start = async (event) => {
    message.replaceChildren();
    const config = {
      names: [app.name],
      kind: kind.value,
      versions: versions.value === "this" && app.version ? { mode: "list", values: [app.version] } : { mode: "any" },
      command: command.value,
    };
    try {
      const result = await busy(event.target, () => api("POST", "/api/removals/ondemand", { config, device_id: device.id, confirm: confirm.value }));
      showRemoval(result.rule.id);
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  drawKind();
  show(
    h("div", { class: "head" }, h("h1", {}, t("removal.ondemand_title")), button(t("action.cancel"), () => showDevice(device.id), "ghost")),
    h("div", { class: "notice" }, t("removal.warning")),
    h(
      "div",
      { class: "panel" },
      h("dl", { class: "facts" }, h("dt", {}, t("inventory.device")), h("dd", {}, device.name), h("dt", {}, t("removal.software")), h("dd", {}, app.name), h("dt", {}, t("apps.version")), h("dd", {}, app.version || "—")),
    ),
    h("div", { class: "panel" }, h("h2", {}, t("removal.how")), field(t("removal.versions_label"), versions), field(t("removal.kind"), kind), kindHint, commandBlock),
    h("div", { class: "panel" }, h("h2", {}, t("removal.confirm_title")), h("p", { class: "muted" }, t("removal.ondemand_text")), field(t("removal.confirm_label_one"), confirm), message, h("div", { class: "row end" }, button(t("removal.ondemand_publish"), start, "danger"))),
  );
}

// Intune only targets groups: devices picked here go into an Entra group kept for the rule.
function devicePicker(rule) {
  const body = h("div", {});
  const message = h("div", {});
  const load = async (control) => {
    let devices;
    try {
      devices = (await busy(control, () => api("GET", `/api/removals/${rule.id}/devices`))).devices;
    } catch (error) {
      message.replaceChildren(errorBox(error));
      return;
    }
    if (!devices.length) {
      body.replaceChildren(h("p", { class: "muted" }, t("removal.devices_none")));
      return;
    }
    const boxes = new Map();
    const rows = devices.map((device) => {
      const box = h("input", { type: "checkbox", checked: device.chosen, disabled: device.chosen, "aria-label": device.name });
      boxes.set(device.id, box);
      return h("tr", {}, h("td", {}, box), h("td", {}, device.name), h("td", {}, device.user || "—"), h("td", {}, device.version || "—"), h("td", {}, when(device.last_sync)));
    });
    const pick = async (event) => {
      message.replaceChildren();
      const ids = devices.filter((device) => !device.chosen && boxes.get(device.id).checked).map((device) => device.id);
      try {
        const result = await busy(event.target, () => api("POST", `/api/removals/${rule.id}/devices`, { device_ids: ids }));
        if (result.missing.length) {
          message.replaceChildren(h("div", { class: "notice" }, t("removal.devices_missing", { names: result.missing.join(", ") })));
          load(null);
        } else {
          showRemoval(rule.id);
        }
      } catch (error) {
        message.replaceChildren(errorBox(error));
      }
    };
    body.replaceChildren(
      table(["", t("inventory.device"), t("inventory.user"), t("apps.version"), t("inventory.last_sync")], rows),
      h("div", { class: "row end" }, button(t("removal.devices_pick"), pick, "danger")),
    );
  };
  const chosen = rule.device_group ? rule.device_group.devices.map((device) => device.name).join(", ") : "";
  body.append(h("div", { class: "row end" }, button(t("removal.devices_load"), (event) => load(event.target), "ghost")));
  return h(
    "div",
    { class: "panel" },
    h("h2", {}, t("removal.devices_title")),
    h("p", { class: "muted" }, t("removal.devices_text")),
    chosen ? h("p", {}, t("removal.devices_chosen", { names: chosen })) : null,
    message,
    body,
  );
}

async function showRemoval(ruleId) {
  drawNav("removals");
  show(h("p", { class: "muted" }, t("apps.loading")));
  let detail;
  try {
    detail = await api("GET", `/api/removals/${encodeURIComponent(ruleId)}`);
  } catch (error) {
    show(errorBox(error), button(t("action.back"), showRemovals, "ghost"));
    return;
  }
  const { rule, results } = detail;
  const onDemand = rule.mechanism === "remediation";
  const message = h("div", {});
  const targets = stageEditor(t("removal.targets"), t("removal.targets_hint"), { targets: rule.targets }, { noIntent: true });
  const badge = (state) => h("span", { class: `badge ${state === "removed" ? "ok" : state === "failed" ? "failed" : ""}` }, t(`removal.state_${state}`));
  const counts = results.available
    ? Object.entries(results.counts)
        .map(([state, count]) => `${t(`removal.state_${state}`)} : ${count}`)
        .join(" · ")
    : "";
  const finish = async (event) => {
    if (!window.confirm(t("removal.finish_confirm", { name: rule.label }))) return;
    try {
      await busy(event.target, () => api("POST", `/api/removals/${rule.id}/finish`));
      showRemovals();
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  const saveTargets = async (event) => {
    try {
      await busy(event.target, () => api("PUT", `/api/removals/${rule.id}/targets`, { targets: targets.value().targets }));
      showRemoval(rule.id);
    } catch (error) {
      message.replaceChildren(errorBox(error));
    }
  };
  const resultsBody = results.available
    ? [
        h("p", { class: "muted" }, counts || t("removal.no_result")),
        results.rows.length
          ? table(
              [t("inventory.device"), t("inventory.user"), t("apps.status"), t(onDemand ? "removal.output" : "removal.error"), t("inventory.last_sync")],
              results.rows.map((row) =>
                h("tr", {}, h("td", {}, row.device), h("td", {}, row.user || "—"), h("td", {}, badge(row.state)), h("td", { class: "mono" }, (onDemand ? row.output : row.error) || "—"), h("td", {}, when(row.last_sync))),
              ),
            )
          : null,
        results.counts.failed ? h("p", { class: "hint" }, t("removal.failed_hint")) : null,
      ]
    : [h("div", { class: "notice" }, t("removal.results_unavailable"), " ", results.detail)];
  show(
    h(
      "div",
      { class: "head" },
      h("h1", {}, rule.config.names.join(", ")),
      button(t("action.back"), showRemovals, "ghost"),
      onDemand ? null : button(t("removal.edit"), () => showRemovalForm({ names: rule.config.names, known: [] }, rule), "ghost"),
      button(t("removal.finish"), finish, "danger"),
    ),
    message,
    h(
      "div",
      { class: "panel" },
      h(
        "dl",
        { class: "facts" },
        h("dt", {}, t("removal.versions")), h("dd", {}, describeVersions(rule.config.versions)),
        h("dt", {}, t("removal.kind")), h("dd", {}, t(`removal.kind_${rule.config.kind}`)),
        h("dt", {}, t("removal.command_short")), h("dd", { class: rule.config.command ? "mono" : "" }, rule.config.command || t("removal.command_auto")),
        h("dt", {}, t("removal.duration")), h("dd", {}, t(onDemand ? "removal.on_demand_long" : rule.permanent ? "removal.permanent_long" : "removal.once_long")),
        h("dt", {}, t("removal.created")), h("dd", {}, `${when(rule.created_at)} — ${rule.created_by}`),
        h("dt", {}, t("removal.remaining")), h("dd", {}, t("removal.remaining_value", { count: detail.remaining })),
      ),
    ),
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("removal.results")),
      resultsBody,
      detail.portal_url ? h("p", {}, h("a", { href: detail.portal_url, target: "_blank", rel: "noopener noreferrer" }, t("removal.open_portal"))) : h("p", { class: "hint" }, t("removal.on_demand_hint")),
    ),
    onDemand ? null : h("div", { class: "panel" }, h("h2", {}, t("removal.where")), targets.node, h("div", { class: "row end" }, button(t("removal.save_targets"), saveTargets, "ghost")), notes(t("removal.save_targets_hint"))),
    !onDemand && config.device_groups ? devicePicker(rule) : null,
  );
}

// ---------------------------------------------------------------- start

async function start() {
  config = await (await fetch("/api/config")).json();
  fallback = await loadLanguage("en");
  const language = document.getElementById("language");
  language.replaceChildren(...Object.entries(config.languages).map(([code, name]) => h("option", { value: code }, name)));
  language.addEventListener("change", async () => {
    await setLanguage(language.value);
    boot();
  });
  document.getElementById("logout").addEventListener("click", async () => {
    try {
      await api("POST", "/auth/logout");
    } finally {
      showLogin(null);
    }
  });
  document.getElementById("brand").addEventListener("click", (event) => {
    event.preventDefault();
    boot();
  });
  await setLanguage(preferredLanguage());
  boot();
}

async function boot() {
  const loginError = new URLSearchParams(location.search).get("login_error");
  if (loginError) {
    history.replaceState(null, "", "/");
    showLogin(loginError);
    return;
  }
  let me;
  try {
    const response = await fetch("/api/me");
    if (!response.ok) return showLogin(null);
    me = await response.json();
  } catch {
    return showLogin("error.network");
  }
  document.getElementById("user").textContent = me.name;
  document.getElementById("logout").title = `${me.name} (${me.login})`;
  document.getElementById("logout").hidden = false;
  if (section === "inventory" && config.device_inventory) showInventory("software");
  else if (section === "removals" && config.device_inventory) showRemovals();
  else showApps();
}

start();
