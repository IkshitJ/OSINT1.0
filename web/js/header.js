// Top bar: the IST clock and one status pill per data source.

const clockFormat = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  weekday: "short",
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function startClock(element) {
  const tick = () => { element.textContent = `${clockFormat.format(new Date())} IST`; };
  tick();
  setInterval(tick, 1000);
}

// source: "sensor" | "headcounter" | "dashboard"
export function setSourcePill(source, online, detail = "") {
  const pill = document.querySelector(`.source-pill[data-source="${source}"]`);
  if (!pill) return;
  pill.classList.toggle("online", online);
  pill.classList.toggle("offline", !online);
  pill.title = online ? "Connected" : `Not connected${detail ? `: ${detail}` : ""}`;
}
