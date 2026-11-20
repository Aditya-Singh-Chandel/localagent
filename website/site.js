// localagent website: three small conveniences. Everything works without JavaScript too.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // 1. A "Copy" button on every code block (shown on hover; always on touch screens).
  for (const block of document.querySelectorAll("pre")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy";
    button.textContent = "Copy";
    button.addEventListener("click", async () => {
      const code = block.querySelector("code") || block;
      try {
        await navigator.clipboard.writeText(code.innerText.replace(/\n$/, ""));
        button.textContent = "Copied";
      } catch {
        button.textContent = "Select and copy";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 1600);
    });
    block.appendChild(button);
  }

  // 2. Not on Windows? Then the main download button leads to the pip instructions.
  const primary = document.getElementById("primary-download");
  const windows = /Windows/i.test(navigator.userAgent);
  if (primary && !windows) {
    primary.setAttribute("href", "#install");
    primary.firstChild.textContent = "Install with pip ";
    const note = primary.querySelector("small");
    if (note) note.textContent = "Linux, macOS · Python 3.10+";
  }

  // 3. Documentation pages: the page list is open on wide screens, folded on small ones.
  const nav = document.getElementById("doc-nav");
  if (nav) {
    const wide = window.matchMedia("(min-width: 901px)");
    const fit = () => { nav.open = wide.matches; };
    fit();
    wide.addEventListener("change", fit);
  }
});
