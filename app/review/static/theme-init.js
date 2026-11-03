// Runs before the page paints, so a chosen theme never flashes the other one.
try {
  const t = localStorage.getItem("custidr-theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch { /* storage blocked: follow the system */ }
