// IntuneAppFastDeploy - single page interface, no build step, no dependency.
// All texts come from /locales/<language>.json: adding a language = adding a file.

const main = document.getElementById("main");
let config = {};
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
  node.disabled = true;
  try {
    return await action();
  } finally {
    node.disabled = false;
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

async function showApps() {
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

function stageEditor(title, hint, stage) {
  const state = { intent: stage?.intent || "required", targets: [...(stage?.targets || [])] };
  const chips = h("div", { class: "chips" });
  const message = h("div", {});
  const draw = () => {
    chips.replaceChildren(
      ...(state.targets.length
        ? state.targets.map((target, index) =>
            h(
              "span",
              { class: "chip" },
              targetLabel(target),
              h("button", { type: "button", "aria-label": t("action.remove"), onclick: () => { state.targets.splice(index, 1); draw(); } }, "×"),
            ),
          )
        : [h("span", { class: "muted" }, t("rollout.no_group"))]),
    );
  };
  const add = (target) => {
    message.replaceChildren();
    const key = target.type === "group" ? target.id.toLowerCase() : target.type;
    if (target.type === "group" && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(target.id)) {
      message.replaceChildren(h("div", { class: "notice error" }, t("error.bad_group_id")));
      return;
    }
    if (!state.targets.some((item) => (item.type === "group" ? item.id.toLowerCase() : item.type) === key)) {
      state.targets.push(target);
    }
    draw();
  };
  const intent = select([["required", t("intent.required")], ["available", t("intent.available")]], state.intent, {
    onchange: (event) => { state.intent = event.target.value; },
  });
  const groupId = input("", { placeholder: t("rollout.group_id"), class: "mono grow" });
  const groupName = input("", { placeholder: t("rollout.group_name"), class: "grow" });
  const manual = h(
    "div",
    { class: "row" },
    groupId,
    groupName,
    button(t("action.add_group"), () => {
      add({ type: "group", id: groupId.value.trim(), name: groupName.value.trim() });
      groupId.value = "";
      groupName.value = "";
    }, "ghost small"),
  );
  let picker = null;
  if (config.group_picker) {
    const results = h("div", { class: "results" });
    const query = h("input", { type: "search", placeholder: t("rollout.search_group"), class: "grow" });
    let timer;
    query.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const found = (await api("GET", `/api/groups?q=${encodeURIComponent(query.value)}`)).groups;
          results.replaceChildren(
            ...(found.length
              ? found.map((group) => button(group.name, () => add({ type: "group", id: group.id, name: group.name }), "linkish"))
              : [h("span", { class: "muted" }, t("rollout.no_result"))]),
          );
        } catch (error) {
          results.replaceChildren(errorBox(error));
        }
      }, 300);
    });
    picker = h("div", {}, query, results);
  }
  draw();
  const node = h(
    "div",
    { class: "stage" },
    h("h3", {}, title),
    hint ? h("p", { class: "hint" }, hint) : null,
    field(t("rollout.intent"), intent),
    chips,
    message,
    picker,
    picker ? null : manual,
    picker ? h("details", {}, h("summary", { class: "muted" }, t("rollout.manual")), manual) : null,
    h(
      "div",
      { class: "row" },
      button(t("target.all_devices"), () => add({ type: "allDevices" }), "ghost small"),
      button(t("target.all_users"), () => add({ type: "allUsers" }), "ghost small"),
    ),
  );
  return { node, value: () => ({ intent: state.intent, targets: state.targets }) };
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
  const assignmentText = (item) => {
    const label = { allDevices: t("target.all_devices"), allUsers: t("target.all_users"), exclusion: t("target.exclusion") }[item.type];
    const who = item.id ? `${label ? `${label}: ` : ""}${item.name ? `${item.name} (${item.id})` : item.id}` : label || t("target.other");
    return `${t(`intent.${item.intent}`)} — ${who}`;
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
        h("dt", {}, t("app.previous")),
        h("dd", {}, detail.older.length ? detail.older.map((item) => `${item.name} ${item.version}`).join(", ") : t("app.none")),
        h("dt", {}, t("app.assignments")),
        h("dd", {}, detail.assignments.length ? h("ul", {}, detail.assignments.map((item) => h("li", {}, assignmentText(item)))) : t("app.none")),
      ),
    ),
    h(
      "div",
      { class: "panel" },
      h("h2", {}, t("rollout.title")),
      h("p", { class: "muted" }, t("rollout.text")),
      pilot.node,
      prod.node,
      message,
      h(
        "div",
        { class: "row end" },
        button(t("action.save_groups"), async (event) => {
          try {
            await busy(event.target, () => api("PUT", `/api/apps/${appId}/rollout`, { name: app.name, pilot: pilot.value(), prod: prod.value() }));
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
    ),
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

function showReview(job, appId) {
  const { analysis, form, existing, older, rollout } = job.result;
  const isUpdate = !!existing;
  const controls = {
    display_name: input(form.display_name),
    version: input(form.version),
    publisher: input(form.publisher),
    description: h("textarea", { value: form.description || "" }),
    install_command: input(form.install_command, { class: "mono" }),
    uninstall_command: input(form.uninstall_command, { class: "mono" }),
  };
  const detection = detectionEditor(form.detection, analysis, isUpdate);
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
    const superseding = h(
      "div",
      {},
      field(t("mode.type"), supersedence, t("mode.type_hint")),
      field(t("rollout.where"), target, hasPilot ? t("rollout.where_hint") : t("rollout.no_pilot_hint")),
      older.length
        ? h("label", { class: "check" }, cleanup, h("span", {}, t("mode.cleanup", { names: older.map((item) => `${item.name} ${item.version}`).join(", ") })))
        : null,
    );
    const inplace = h("p", { class: "hint", hidden: true }, t("mode.inplace_hint"));
    mode.addEventListener("change", () => {
      superseding.hidden = mode.value !== "supersede";
      inplace.hidden = mode.value !== "inplace";
    });
    options = h("div", { class: "panel" }, h("h2", {}, t("mode.title")), field(t("mode.label"), mode), superseding, inplace);
    extra = () => ({ mode: mode.value, supersedence_type: supersedence.value, rollout: target.value, cleanup_old: cleanup.checked });
  } else {
    const architecture = select([["any", t("arch.any")], ["x64", "x64"], ["x86", "x86"], ["arm64", "ARM64"]], form.architecture);
    const runAs = select([["system", t("run_as.system")], ["user", t("run_as.user")]], form.run_as);
    const pilot = stageEditor(t("rollout.pilot"), t("rollout.pilot_hint"), rollout.pilot);
    const prod = stageEditor(t("rollout.prod"), t("rollout.prod_hint"), rollout.prod);
    const target = select([["none", t("rollout.to_none")], ["pilot", t("rollout.to_pilot")], ["prod", t("rollout.to_prod")]], "none");
    options = h(
      "div",
      { class: "panel" },
      h("h2", {}, t("rollout.title")),
      h("div", { class: "two" }, h("div", {}, field(t("form.architecture"), architecture)), h("div", {}, field(t("form.run_as"), runAs))),
      pilot.node,
      prod.node,
      field(t("rollout.where"), target),
    );
    extra = () => ({ mode: "new", architecture: architecture.value, run_as: runAs.value, pilot: pilot.value(), prod: prod.value(), rollout: target.value });
  }

  const publish = async (event) => {
    message.replaceChildren();
    const body = { ...extra(), detection: detection.value() };
    for (const [key, control] of Object.entries(controls)) body[key] = control.value;
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
  document.getElementById("logout").hidden = false;
  showApps();
}

start();
