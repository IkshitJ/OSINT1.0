// The overcrowding log panel: every time a place went over its limit or came
// back under it, newest first, from both the Sensor gates and the CCTV cameras.

const MAX_EVENTS = 500;
const SOURCE_LABELS = { sensor: "Sensor gates", headcounter: "CCTV" };

const timeFormat = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function createAlertLog({ listEl, emptyEl, activeCountEl, filtersEl }) {
  let events = []; // oldest first, as the server sends them
  let filter = "all";

  filtersEl.addEventListener("click", (click) => {
    const button = click.target.closest("button[data-filter]");
    if (!button) return;
    filter = button.dataset.filter;
    for (const b of filtersEl.querySelectorAll("button")) b.classList.toggle("on", b === button);
    render();
  });

  function render() {
    const visible = events.filter((e) => filter === "all" || e.source === filter);
    listEl.replaceChildren(...visible.slice().reverse().map(renderEvent));
    emptyEl.hidden = visible.length > 0;

    const active = countActive(events);
    activeCountEl.textContent = `${active} active`;
    activeCountEl.classList.toggle("has-active", active > 0);
  }

  return {
    replaceAll(allEvents) {
      events = allEvents.slice(-MAX_EVENTS);
      render();
    },
    add(event) {
      events.push(event);
      if (events.length > MAX_EVENTS) events.shift();
      render();
    },
  };
}

// A place is "active" while its most recent event is a raised alert.
function countActive(events) {
  const latestByPlace = new Map();
  for (const e of events) latestByPlace.set(`${e.source}|${e.place}`, e.state);
  return [...latestByPlace.values()].filter((state) => state === "raised").length;
}

function renderEvent(event) {
  const item = document.createElement("li");
  item.className = `alert ${event.state}`;

  const head = document.createElement("div");
  head.className = "alert-head";
  head.append(
    span("alert-state", event.state === "raised" ? "OVERCROWDED" : "CLEARED"),
    span("alert-place", event.place),
    span("alert-count", `${event.count.toLocaleString("en-IN")} / ${event.limit.toLocaleString("en-IN")}`),
  );

  const message = document.createElement("p");
  message.className = "alert-message";
  message.textContent = event.message;

  const meta = span("alert-meta", `${SOURCE_LABELS[event.source] ?? event.source} · ${timeFormat.format(event.time_ms)} IST`);

  item.append(head, message, meta);
  return item;
}

function span(className, text) {
  const el = document.createElement("span");
  el.className = className;
  el.textContent = text;
  return el;
}
