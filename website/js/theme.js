// Sets the colour theme before the page is drawn, so there is never a flash of the wrong one.
// A theme you chose with the switch is remembered; otherwise the system's preference is followed.
(function () {
  var theme = null;
  try {
    theme = localStorage.getItem("theme");
  } catch (error) {
    theme = null;                                  // storage blocked: follow the system
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", theme);
})();
