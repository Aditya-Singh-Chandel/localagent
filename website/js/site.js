// localagent website. Everything here is an extra: every page works and reads fine without it.
// Nothing here ever navigates, downloads or changes a link by itself - only what you click does.
"use strict";

const ROOT = document.documentElement.dataset.root || "";
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---- colour theme ---------------------------------------------------------------------------------
function initTheme() {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  const label = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    button.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme");
  };
  label();
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (error) { /* not remembered: fine */ }
    label();
  });
}

// ---- menu on small screens --------------------------------------------------------------------------
function initMenu() {
  const button = document.getElementById("menu-button");
  const menu = document.getElementById("mobile-nav");
  if (!button || !menu) return;
  const set = (open) => {
    menu.classList.toggle("open", open);
    button.setAttribute("aria-expanded", String(open));
  };
  button.addEventListener("click", () => set(!menu.classList.contains("open")));
  menu.addEventListener("click", (event) => { if (event.target.closest("a")) set(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") set(false); });
}

// ---- copy buttons on code blocks --------------------------------------------------------------------
function addCopyButtons(scope) {
  for (const block of scope.querySelectorAll("pre")) {
    if (block.closest(".terminal") || block.querySelector(".copy")) continue;
    const button = element("button", "copy", "Copy");
    button.type = "button";
    button.setAttribute("aria-label", "Copy this code");
    button.addEventListener("click", async () => {
      const code = block.querySelector("code") || block;
      const text = code.innerText.replace(/\n$/, "");
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch (error) {
        button.textContent = "Press Ctrl+C";
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      button.classList.add("done");
      setTimeout(() => { button.textContent = "Copy"; button.classList.remove("done"); }, 1800);
    });
    block.appendChild(button);
  }
}

// ---- sections rise into view ------------------------------------------------------------------------
function initReveal() {
  const items = [...document.querySelectorAll(".reveal")];
  if (!items.length || REDUCED_MOTION || !("IntersectionObserver" in window)) return;
  for (const item of items) {                    // already on screen: never hide it, not even briefly
    if (item.getBoundingClientRect().top < window.innerHeight) item.classList.add("visible");
  }
  document.documentElement.classList.add("reveal-ready");
  const watcher = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        watcher.unobserve(entry.target);
      }
    }
  }, { rootMargin: "0px 0px -8% 0px" });
  items.forEach((item) => watcher.observe(item));
}

// ---- the session player: real recorded sessions, replayed ----------------------------------------
function initPlayer() {
  const player = document.querySelector("[data-player]");
  const data = document.getElementById("sessions-data");
  if (!player || !data) return;
  const sessions = JSON.parse(data.textContent);
  const body = player.querySelector(".term-body");
  const title = player.querySelector(".term-title");
  const summary = player.querySelector(".summary");
  const tabs = [...player.querySelectorAll("[role=tab]")];
  const pause = player.querySelector("[data-action=pause]");
  const replay = player.querySelector("[data-action=replay]");
  let run = 0;                                     // a new run makes an older one stop
  let paused = false;
  let current = sessions[0];
  let started = false;

  const until = async (token) => {
    while (paused && token === run) await sleep(120);
  };

  function line(kind, text, extra) {
    const row = element("div", kind === "user" || kind === "cmd" ? "" : kind);
    if (kind === "user" || kind === "cmd") {
      row.appendChild(element("span", "prompt", kind === "user" ? "you> " : "$ "));
      row.appendChild(element("span", kind === "user" ? "user" : "cmd", text));
    } else if (kind === "tool") {
      row.appendChild(element("span", "tool", text));
      row.appendChild(element("span", "args", extra || ""));
    } else {
      row.textContent = text;
    }
    return row;
  }

  function showAll(session) {
    body.textContent = "";
    for (const [kind, text, extra] of session.lines) body.appendChild(line(kind, text, extra));
  }

  async function type(target, text, perChar, token) {
    const caret = element("span", "caret");
    target.after(caret);
    const step = Math.max(4, Math.min(perChar, 1500 / Math.max(text.length, 1)));
    for (let i = 0; i < text.length; i += 1) {
      if (token !== run) { caret.remove(); return; }
      await until(token);
      target.textContent += text[i];
      if (i % 3 === 0) body.scrollTop = body.scrollHeight;
      await sleep(step);
    }
    caret.remove();
  }

  const pauses = { meta: 70, tool: 450, out: 60, ok: 260, fail: 260, add: 50, del: 50, answer: 120 };

  async function play(session) {
    const token = ++run;
    current = session;
    paused = false;
    pause.querySelector("span").textContent = "Pause";
    pause.setAttribute("aria-pressed", "false");
    pause.disabled = false;
    title.textContent = "localagent — " + session.folder;
    summary.textContent = session.summary;
    tabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.session === session.id)));
    if (REDUCED_MOTION) { showAll(session); pause.disabled = true; return; }
    body.textContent = "";
    for (const [kind, text, extra] of session.lines) {
      if (token !== run) return;
      await until(token);
      if (kind === "user" || kind === "cmd") {
        const row = line(kind, "", extra);
        body.appendChild(row);
        await sleep(350);
        await type(row.lastChild, text, 26, token);
        await sleep(300);
      } else if (kind === "answer") {
        const row = line(kind, "");
        body.appendChild(row);
        await type(row, text, 9, token);
      } else {
        await sleep(pauses[kind] || 80);
        body.appendChild(line(kind, text, extra));
      }
      body.scrollTop = body.scrollHeight;
    }
    if (token === run) pause.disabled = true;      // finished: nothing left to pause
  }

  tabs.forEach((tab) => tab.addEventListener("click", () => {
    started = true;
    play(sessions.find((session) => session.id === tab.dataset.session));
  }));
  replay.addEventListener("click", () => { started = true; play(current); });
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.querySelector("span").textContent = paused ? "Resume" : "Pause";
    pause.setAttribute("aria-pressed", String(paused));
  });
  if ("IntersectionObserver" in window) {        // the first session plays when you scroll to it
    const watcher = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !started) {
        started = true;
        watcher.disconnect();
        play(sessions[0]);
      }
    }, { threshold: 0.35 });
    watcher.observe(player);
  }
}

// ---- download page: point out the file for this computer (links never change) ---------------------
function initDownloads() {
  const cards = document.querySelectorAll("[data-platform]");
  if (!cards.length) return;
  const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent;
  const mine = /win/i.test(platform) ? "windows" : /mac|linux|x11|cros/i.test(platform) ? "unix" : "";
  cards.forEach((card) => card.classList.toggle("mine", card.dataset.platform === mine));
  for (const link of document.querySelectorAll("a[data-download]")) {
    link.addEventListener("click", () => {
      const note = link.closest("[data-platform]").querySelector(".download-note");
      if (note) note.hidden = false;
    });
  }
}

// ---- setup builder ----------------------------------------------------------------------------------
function initBuilder() {
  const form = document.getElementById("builder");
  const data = document.getElementById("setup-data");
  const out = document.getElementById("builder-result");
  if (!form || !data || !out) return;
  const info = JSON.parse(data.textContent);
  const byId = (list, id) => list.find((item) => item.id === id);

  function read() {
    const value = (name) => (form.querySelector(`input[name="${name}"]:checked`) || {}).value;
    return { os: value("os"), online: value("online"), offline: value("offline"), ram: value("ram") };
  }

  function restore() {
    const state = new URLSearchParams(location.hash.slice(1));
    for (const [name, choice] of state) {
      const input = form.querySelector(`input[name="${name}"][value="${CSS.escape(choice)}"]`);
      if (input) input.checked = true;
    }
  }

  function code(lines) {
    const pre = element("pre");
    pre.appendChild(element("code", "", lines.join("\n")));
    return pre;
  }

  function step(list, heading, ...parts) {
    const item = element("li");
    item.appendChild(element("h3", "", heading));
    for (const part of parts) item.appendChild(typeof part === "string" ? element("p", "", part) : part);
    list.appendChild(item);
  }

  function variables(os, pairs) {
    if (!pairs.length) return null;
    if (os === "windows") return code(pairs.map(([name, value]) => `setx ${name} "${value}"`));
    return code(pairs.map(([name, value]) => `echo 'export ${name}="${value}"' >> ~/.profile`));
  }

  function render() {
    const choice = read();
    const online = byId(info.online, choice.online);
    const offline = byId(info.local, choice.offline);
    const ramBox = document.getElementById("ram-choice");
    ramBox.hidden = choice.offline !== "download";
    const model = info.models.find((entry) => Number(choice.ram) >= entry.memory) || info.models[info.models.length - 1];
    history.replaceState(null, "", "#" + new URLSearchParams(choice).toString());

    out.textContent = "";
    const chips = element("div", "result-summary");
    chips.appendChild(element("span", "badge", { windows: "Windows", mac: "macOS", linux: "Linux" }[choice.os]));
    chips.appendChild(element("span", "badge", online ? online.title : "No online model"));
    chips.appendChild(element("span", "badge green", offline ? offline.title : "No offline model"));
    out.appendChild(chips);
    const list = element("ol");

    if (choice.os === "windows") {
      const link = element("a", "", "the Download page");
      link.href = ROOT + "download.html";
      const p = element("p", "", `Get ${info.zip} (${info.zip_size}, Python included) from `);
      p.append(link, ". Unzip it somewhere permanent, for example C:\\Tools\\localagent, and double-click install.cmd: it adds localagent to your PATH and starts the guided setup right away.");
      step(list, "Install localagent", p);
    } else {
      const shell = choice.os === "mac" ? "Terminal" : "a terminal";
      step(list, "Install localagent",
        `Python 3.10 or newer is needed (${choice.os === "mac" ? "python.org or Homebrew" : "your package manager"}). Download the wheel from the Download page, then in ${shell}:`,
        code(["python3 -m pip install --user pipx", "python3 -m pipx ensurepath", `pipx install ~/Downloads/${info.wheel}`]));
    }

    const pairs = [];
    if (online && online.id !== "none") {
      if (online.id === "custom") {
        step(list, "Have the address of your service ready",
          "It usually ends in /v1 (for example https://api.together.xyz/v1). If the service needs a key, have that too.");
      } else {
        const p = element("p", "", `${online.title}: ${online.note}. Make a key at `);
        const link = element("a", "", online.page.replace(/^https:\/\//, ""));
        link.href = online.page;
        link.rel = "noopener";
        p.append(link, online.id === "gemini" ? " — sign in with Google, then “Create API key”." : ".");
        step(list, online.id === "gemini" ? "Get your free Gemini key" : `Get your ${online.title} key`, p);
      }
      pairs.push(["LOCALAGENT_PROVIDER", online.id]);
      if (online.id !== "custom") pairs.push([online.env, "your-key"]);
      if (online.model) pairs.push(["LOCALAGENT_PROVIDER_MODEL", online.model]);
      if (online.id === "custom") pairs.push(["LOCALAGENT_PROVIDER_BASE", "https://your-service/v1"], ["LOCALAGENT_PROVIDER_MODEL", "model-name"]);
    }

    if (offline && offline.id === "download") {
      step(list, "Nothing to prepare for the offline model",
        `Setup offers “download a free model for me”. For your computer's memory it suggests ${model.name} (${model.size} GB download), checked against its publisher's checksum, and a broken download continues where it stopped.`);
    } else if (offline && offline.id !== "none") {
      step(list, `Start ${offline.title}`, offline.how, ...(offline.command ? [code([offline.command])] : []));
      pairs.push(["LOCALAGENT_LOCAL_SERVER", offline.kind]);
      if (offline.kind === "openai" && offline.base) pairs.push(["LOCALAGENT_API_BASE", offline.base]);
      pairs.push(["LOCALAGENT_MODEL", offline.model]);
    }

    const answers = [];
    if (online && online.id !== "none") answers.push(`Step 1: choose ${online.menu}) ${online.menu_title}` + (online.id === "custom" ? " and type its address" : "") + ".");
    else answers.push(`Step 1: choose ${info.skip_number}) skip.`);
    if (online && !["none", "custom"].includes(online.id)) answers.push("Paste the key when asked: it stays hidden, and it is checked before anything is saved.");
    if (offline && offline.id !== "none") answers.push(`Step 2: choose “${offline.menu_label}”.`);
    else answers.push("Step 2: choose skip.");
    step(list, choice.os === "windows" ? "Answer the setup's questions" : "Run the setup", ...(choice.os === "windows" ? [] : [code(["localagent setup"])]), answers.join(" "));

    step(list, "Start working",
      "Open a terminal in a project folder and start the chat. Check everything at any time with --doctor.",
      code(["cd my-project", "localagent", "localagent --doctor"]));

    out.appendChild(list);
    const manual = variables(choice.os, pairs);
    if (manual) {
      const box = element("details", "note-box");
      box.appendChild(element("summary", "", "Or set it without questions (for scripts)"));
      box.appendChild(element("p", "", choice.os === "windows"
        ? "These settings do what the setup would save. Open a new terminal afterwards."
        : "These settings do what the setup would save. Open a new terminal afterwards."));
      box.appendChild(manual);
      out.appendChild(box);
    }
    const privacy = element("p", "note-box");
    privacy.textContent = !online || online.id === "none"
      ? "Privacy: everything stays on your computer — nothing is sent anywhere."
      : `Privacy: the parts of your project the agent reads are sent to ${online.title}, under its terms${online.id === "gemini" ? " (on the free tier Google may use them to improve its products)" : ""}. Offline mode sends nothing.`;
    out.appendChild(privacy);
    addCopyButtons(out);
  }

  restore();
  form.addEventListener("change", render);
  render();
}

// ---- search ---------------------------------------------------------------------------------------
function initSearch() {
  const dialog = document.getElementById("search-dialog");
  const openers = document.querySelectorAll("[data-search-open]");
  if (!dialog || typeof dialog.showModal !== "function") {
    openers.forEach((button) => { button.hidden = true; });
    return;
  }
  const input = dialog.querySelector("input");
  const list = dialog.querySelector(".search-results");
  let index = null;
  let shown = [];
  let selected = 0;

  async function load() {
    if (index) return index;
    try {
      const response = await fetch(ROOT + "search-index.json");
      index = await response.json();
    } catch (error) {
      index = [];
      list.textContent = "";
      list.appendChild(element("li", "search-empty", "Search works once the site is served by a web server."));
    }
    return index;
  }

  function marked(text, words) {
    const span = element("span");
    const lower = text.toLowerCase();
    let at = 0;
    while (at < text.length) {
      let next = -1;
      let length = 0;
      for (const word of words) {
        const found = lower.indexOf(word, at);
        if (found !== -1 && (next === -1 || found < next)) { next = found; length = word.length; }
      }
      if (next === -1) { span.append(text.slice(at)); break; }
      span.append(text.slice(at, next));
      span.appendChild(element("mark", "", text.slice(next, next + length)));
      at = next + length;
    }
    return span;
  }

  function render(entries, words) {
    shown = entries;
    selected = 0;
    list.textContent = "";
    if (!words.length) {
      list.appendChild(element("li", "search-empty", "Type to search every page of the documentation."));
      return;
    }
    if (!entries.length) {
      list.appendChild(element("li", "search-empty", "Nothing found. Try fewer or other words."));
      return;
    }
    entries.forEach((entry, number) => {
      const item = element("li");
      const link = element("a");
      link.href = ROOT + entry.u;
      link.setAttribute("aria-selected", String(number === 0));
      link.appendChild(element("strong", "", entry.h || entry.p));
      link.appendChild(element("small", "", entry.h ? entry.p : "Page"));
      if (entry.x) link.appendChild(marked(entry.x, words));
      item.appendChild(link);
      list.appendChild(item);
    });
  }

  function search(query) {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const scored = [];
    for (const entry of words.length ? index : []) {
      const page = entry.p.toLowerCase();
      const heading = (entry.h || "").toLowerCase();
      const text = (entry.x || "").toLowerCase();
      let score = entry.g ? 9 : 0;                 // user guides first, the code reference after
      let every = true;
      for (const word of words) {
        const points = (heading.includes(word) ? 8 : 0) + (page.includes(word) ? 4 : 0) + (text.includes(word) ? 1 : 0);
        if (!points) { every = false; break; }
        score += points;
      }
      if (every) scored.push([score, entry]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    render(scored.slice(0, 25).map((pair) => pair[1]), words);
  }

  function choose(step) {
    const links = list.querySelectorAll("a");
    if (!links.length) return;
    selected = (selected + step + links.length) % links.length;
    links.forEach((link, number) => link.setAttribute("aria-selected", String(number === selected)));
    links[selected].scrollIntoView({ block: "nearest" });
  }

  function open() {
    dialog.showModal();
    input.value = "";
    render([], []);
    input.focus();
    load();
  }

  openers.forEach((button) => button.addEventListener("click", open));
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if ((event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) || (event.key === "/" && !typing)) {
      event.preventDefault();
      if (!dialog.open) open();
    }
  });
  input.addEventListener("input", async () => { await load(); search(input.value); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); choose(1); }
    if (event.key === "ArrowUp") { event.preventDefault(); choose(-1); }
    if (event.key === "Enter") {
      const link = list.querySelectorAll("a")[selected];
      if (link) { event.preventDefault(); link.click(); }
    }
  });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}

// ---- documentation: the page list folds on small screens, the contents follow the reading --------
function initDocs() {
  const nav = document.getElementById("docs-nav");
  if (nav) {
    const wide = window.matchMedia("(min-width: 861px)");
    const fit = () => { nav.open = wide.matches; };
    fit();
    wide.addEventListener("change", fit);
  }
  const links = [...document.querySelectorAll(".toc a")];
  if (!links.length || !("IntersectionObserver" in window)) return;
  const byId = new Map(links.map((link) => [decodeURIComponent(link.hash.slice(1)), link]));
  const watcher = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      links.forEach((link) => link.classList.remove("active"));
      const link = byId.get(entry.target.id);
      if (link) link.classList.add("active");
    }
  }, { rootMargin: "-80px 0px -70% 0px" });
  for (const id of byId.keys()) {
    const heading = document.getElementById(id);
    if (heading) watcher.observe(heading);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  for (const start of [initReveal, initTheme, initMenu, () => addCopyButtons(document), initPlayer,
                       initDownloads, initBuilder, initSearch, initDocs]) {
    try {
      start();
    } catch (error) {
      console.error(error);                        // one broken part must not stop the others
    }
  }
});
