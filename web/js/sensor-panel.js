// The sensor-gates panel: a tab per ghat, the chosen ghat's live numbers, and
// its gate simulation (the scene from /sensor/, embedded).

const NEAR_LIMIT_RATIO = 0.8;
const number = (value) => value.toLocaleString("en-IN");

export function createSensorPanel({ tabsEl, frameEl, openEl, appUrl, ghats, els, onSelect }) {
  let selectedId = null;
  let readings = {}; // ghat id -> { mode, inside, limit, alarm, entered, exited } or null

  const tabs = new Map();
  for (const ghat of ghats) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.role = "tab";
    tab.dataset.level = "unknown";
    tab.innerHTML = "<i></i>";
    tab.append(ghat.name.replace(/ Ghat$/, ""));
    tab.title = `Show the ${ghat.name} gates`;
    tab.addEventListener("click", () => select(ghat.id));
    tabs.set(ghat.id, tab);
  }
  tabsEl.replaceChildren(...tabs.values());

  function select(ghatId) {
    if (ghatId === selectedId || !tabs.has(ghatId)) return;
    selectedId = ghatId;
    for (const [id, tab] of tabs) {
      tab.classList.toggle("on", id === ghatId);
      tab.setAttribute("aria-selected", id === ghatId);
    }
    frameEl.src = `${appUrl}?ghat=${ghatId}&embed=1`;
    openEl.href = `${appUrl}?ghat=${ghatId}`;
    renderStats();
    onSelect?.(ghatId);
  }

  function renderStats() {
    const reading = readings[selectedId];
    const counting = reading?.mode === "counting";
    els.inside.textContent = counting ? number(reading.inside) : "–";
    els.limit.textContent = counting ? `/ ${number(reading.limit)}` : "";
    els.entered.textContent = counting ? number(reading.entered) : "–";
    els.exited.textContent = counting ? number(reading.exited) : "–";
    const level = levelOf(reading);
    els.fill.style.width = counting && reading.limit > 0 ? `${Math.min(100, (reading.inside / reading.limit) * 100)}%` : "0";
    els.stats.dataset.level = level;
    els.state.textContent = { over: "OVERCROWDED", near: "NEAR LIMIT", ok: "OK", unknown: reading?.mode === "calibration" ? "CALIBRATING" : "NO DATA" }[level];
  }

  select(ghats[0].id);

  return {
    select,
    // sites: { ghatId: reading } or null when the sensor gates are down
    setReadings(sites) {
      readings = sites ?? {};
      for (const [id, tab] of tabs) tab.dataset.level = levelOf(readings[id]);
      renderStats();
    },
  };
}

export function levelOf(reading) {
  if (reading?.mode !== "counting") return "unknown";
  if (reading.alarm) return "over";
  return reading.limit > 0 && reading.inside / reading.limit >= NEAR_LIMIT_RATIO ? "near" : "ok";
}
