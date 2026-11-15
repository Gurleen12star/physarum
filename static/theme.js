// Theme: dark by default, remembered per browser. Pages listen for "themechange" to redraw canvases.
(function () {
  const root = document.documentElement;
  const read = () => {
    try {
      return localStorage.getItem("physarum_theme");
    } catch {
      return null;
    }
  };
  const save = t => {
    try {
      localStorage.setItem("physarum_theme", t);
    } catch {
      /* storage unavailable */
    }
  };
  root.dataset.theme = read() || "dark";
  window.cssVar = name => getComputedStyle(root).getPropertyValue(name).trim();
  window.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("themeToggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      root.dataset.theme = next;
      save(next);
      window.dispatchEvent(new CustomEvent("themechange", { detail: next }));
    });
  });
})();
