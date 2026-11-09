// Entry point: loads settings from the server, builds each panel and
// routes live events to them.

import { createAlertLog } from "./alert-log.js";
import { createCrowdMap } from "./crowd-map.js";
import { setSourcePill, startClock } from "./header.js";
import { connectLiveEvents } from "./live-events.js";

const byId = (id) => document.getElementById(id);

async function main() {
  startClock(byId("clock"));

  const config = await fetch("/api/config").then((response) => response.json());

  embedApp("sensor", config.sensorAppUrl);
  embedApp("headcounter", config.headcounterAppUrl);

  const map = createCrowdMap({
    container: byId("map"),
    controlsContainer: byId("map-controls"),
    mapConfig: config.map,
    sites: config.sites,
  });

  const alertLog = createAlertLog({
    listEl: byId("alert-list"),
    emptyEl: byId("alert-empty"),
    activeCountEl: byId("active-count"),
    filtersEl: byId("alert-filters"),
  });

  function showSourceStatus(status) {
    setSourcePill(status.source, status.online, status.error ?? "");
    if (status.source === "sensor") {
      const reading = status.online && status.reading?.mode === "counting"
        ? { count: status.reading.inside, limit: status.reading.limit, alarm: status.reading.alarm }
        : null;
      for (const siteId of map.sitesFor("sensor")) map.setSiteReading(siteId, reading);
    }
  }

  connectLiveEvents({
    onSnapshot({ alerts, sources }) {
      alertLog.replaceAll(alerts);
      Object.values(sources).forEach(showSourceStatus);
    },
    onAlert: (event) => alertLog.add(event),
    onSource: showSourceStatus,
    onConnectionChange: (online) => setSourcePill("dashboard", online),
  });
}

function embedApp(name, url) {
  byId(`${name}-frame`).src = url;
  byId(`${name}-open`).href = url;
}

main().catch((error) => {
  console.error("Dashboard failed to start", error);
  document.body.insertAdjacentHTML("afterbegin", '<p style="padding:12px;color:#ef4444">Could not load the dashboard settings. Is the server running?</p>');
});
