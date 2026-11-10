// Entry point: loads settings from the server, builds each panel and
// routes live events to them.

import { createAlertLog } from "./alert-log.js";
import { createCctvPanel } from "./cctv-panel.js";
import { createCrowdMap } from "./crowd-map.js";
import { setSourcePill, startClock } from "./header.js";
import { connectLiveEvents } from "./live-events.js";
import { createSensorPanel } from "./sensor-panel.js";

const byId = (id) => document.getElementById(id);

async function main() {
  startClock(byId("clock"));

  const config = await fetch("/api/config").then((response) => response.json());
  byId("cctv-open").href = config.cctvAppUrl;

  // Picking a ghat on the map and picking its tab are the same thing.
  let sensorPanel;
  const map = createCrowdMap({
    container: byId("map"),
    controlsContainer: byId("map-controls"),
    mapConfig: config.map,
    ghats: config.ghats,
    onSelectGhat: (ghatId) => sensorPanel.select(ghatId),
  });

  sensorPanel = createSensorPanel({
    tabsEl: byId("ghat-tabs"),
    frameEl: byId("sensor-frame"),
    openEl: byId("sensor-open"),
    appUrl: config.sensorAppUrl,
    ghats: config.ghats,
    els: {
      stats: byId("gate-stats"),
      inside: byId("gate-inside"),
      limit: byId("gate-limit"),
      fill: byId("gate-fill"),
      entered: byId("gate-in"),
      exited: byId("gate-out"),
      state: byId("gate-state"),
    },
    onSelect: (ghatId) => map.highlightGhat(ghatId),
  });

  const cctvPanel = createCctvPanel({ gridEl: byId("camera-grid"), totalEl: byId("cctv-total"), ghats: config.ghats });

  const alertLog = createAlertLog({
    listEl: byId("alert-list"),
    emptyEl: byId("alert-empty"),
    activeCountEl: byId("active-count"),
    filtersEl: byId("alert-filters"),
  });

  function showSourceStatus(status) {
    setSourcePill(status.source, status.online, status.error ?? "");
    if (status.source === "sensor") {
      const sites = status.online ? status.reading?.sites : null;
      sensorPanel.setReadings(sites);
      map.setGateReadings(sites);
    } else if (status.source === "headcounter") {
      const cameras = status.online ? status.reading?.cameras : null;
      cctvPanel.setReadings(cameras);
      map.setCameraReadings(cameras);
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

main().catch((error) => {
  console.error("Dashboard failed to start", error);
  document.body.insertAdjacentHTML("afterbegin", '<p style="padding:12px;color:#ef4444">Could not load the dashboard settings. Is the server running?</p>');
});
