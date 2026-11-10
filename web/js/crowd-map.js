// The situation map: opens on India, then flies to Ujjain's ghats once loaded.
// Shows a marker per ghat with its live gate count and CCTV head count, live
// traffic when a TomTom key is configured, and satellite views on demand.
//
// `maplibregl` is the global from the MapLibre script tag in index.html.

import { addSatelliteView } from "./satellite-view.js";
import { levelOf } from "./sensor-panel.js";
import { addTrafficLayer } from "./traffic-layer.js";

const START_VIEW = { center: [78.9, 22.6], zoom: 3.6 }; // all of India
const FLY_TO_DELAY_MS = 600;
const FLY_TO_DURATION_MS = 4000;
// Room for the legend above the northernmost ghat and for the label under the southernmost.
const GHATS_VIEW = { padding: { top: 110, bottom: 100, left: 90, right: 90 }, maxZoom: 14 };
const LEVEL_RANK = { unknown: 0, ok: 1, near: 2, over: 3 };

export function createCrowdMap({ container, controlsContainer, mapConfig, ghats, onSelectGhat }) {
  const map = new maplibregl.Map({
    container,
    style: mapConfig.styleUrl,
    center: START_VIEW.center,
    zoom: START_VIEW.zoom,
    attributionControl: { compact: true },
  });
  // Compass shows tilt and rotation; click it to face north and flatten again.
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  const legend = container.parentElement.querySelector(".map-legend");
  if (legend) {
    map.addControl({ onAdd: () => legend, onRemove: () => legend.remove() }, "top-left");
    map.resize(); // the legend no longer sits under the map, so the map just got taller
  }

  // "Ujjain" means: every monitored ghat in view.
  const ghatBounds = ghats.reduce((bounds, ghat) => bounds.extend([ghat.lon, ghat.lat]), new maplibregl.LngLatBounds());
  const showAllGhats = () => map.fitBounds(ghatBounds, { ...GHATS_VIEW, duration: FLY_TO_DURATION_MS, essential: true });

  const markers = addGhatMarkers(map, ghats, onSelectGhat);
  map.on("move", () => separateLabels(map, markers));
  const { trafficButton, simButton, satelliteButtons } = addControls(controlsContainer, { onRecenter: showAllGhats, cityName: mapConfig.focusCity.name });
  const notice = createMapNotice(container);

  // data-state on the map element: "loading" -> "flying" -> "ready" (at Ujjain, every tile drawn).
  // Lets automated checks (tests/browser_check.py) wait for the real thing instead of guessing.
  container.dataset.state = "loading";
  map.once("load", () => {
    const firstLabelLayer = map.getStyle().layers.find((layer) => layer.type === "symbol");
    // Satellite first, so traffic drawn after it sits on top of the imagery.
    setupSatellite(map, firstLabelLayer?.id, satelliteButtons);
    setupTraffic(map, mapConfig.traffic, { trafficButton, simButton }, notice, firstLabelLayer?.id);
    setTimeout(() => {
      container.dataset.state = "flying";
      showAllGhats();
      map.once("moveend", () => whenTilesLoaded(map, () => { container.dataset.state = "ready"; }));
    }, FLY_TO_DELAY_MS);
  });

  return {
    // sites: { ghatId: { mode, inside, limit, alarm, ... } } or null when the sensor gates are down
    setGateReadings(sites) {
      for (const marker of markers.values()) {
        marker.gate = sites?.[marker.ghat.id] ?? null;
        renderGhatMarker(marker);
      }
    },
    // cameras: [{ ghat_id, count, limit, has_video }] or null when CCTV is down
    setCameraReadings(cameras) {
      for (const marker of markers.values()) {
        marker.camera = cameras?.find((camera) => camera.ghat_id === marker.ghat.id) ?? null;
        renderGhatMarker(marker);
      }
    },
    highlightGhat(ghatId) {
      for (const [id, marker] of markers) marker.element.classList.toggle("selected", id === ghatId);
    },
  };
}

// Every tile source has drawn the current view. (Not the map's "idle" event:
// the moving vehicles update their data many times a second, so the map is never idle.)
function whenTilesLoaded(map, callback) {
  const check = () => {
    const sources = Object.entries(map.getStyle().sources).filter(([, source]) => source.type !== "geojson");
    if (sources.every(([id]) => map.isSourceLoaded(id))) callback();
    else setTimeout(check, 200);
  };
  check();
}

function addGhatMarkers(map, ghats, onSelectGhat) {
  const markers = new Map();
  for (const ghat of ghats) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "site-marker";
    element.title = `Show the ${ghat.name} gates`;
    element.innerHTML = '<span class="site-marker-body"><span class="site-marker-dot"></span><span class="site-marker-label"><b></b><small></small></span></span>';
    element.addEventListener("click", () => onSelectGhat?.(ghat.id));
    new maplibregl.Marker({ element, anchor: "top" }).setLngLat([ghat.lon, ghat.lat]).addTo(map);
    const marker = { element, ghat, gate: null, camera: null };
    markers.set(ghat.id, marker);
    renderGhatMarker(marker);
  }
  return markers;
}

// Labels hang below their dots. When two ghats are close on screen the label of
// the northern one would cover the southern one's dot, so it goes above instead.
const LABEL_WIDTH_PX = 170;
const LABEL_HEIGHT_PX = 60;
function separateLabels(map, markers) {
  const placed = [...markers.values()].map((marker) => ({ marker, point: map.project([marker.ghat.lon, marker.ghat.lat]) }));
  for (const { marker, point } of placed) {
    const crowdedBelow = placed.some(({ marker: other, point: otherPoint }) =>
      other !== marker &&
      Math.abs(otherPoint.x - point.x) < LABEL_WIDTH_PX &&
      otherPoint.y >= point.y && otherPoint.y - point.y < LABEL_HEIGHT_PX);
    marker.element.classList.toggle("above", crowdedBelow);
  }
}

// The dot shows the worse of the two: the gates' count against their limit, or the camera's.
function renderGhatMarker({ element, ghat, gate, camera }) {
  const counting = gate?.mode === "counting";
  const cameraLive = camera?.has_video;
  const cameraLevel = !cameraLive ? "unknown" : camera.count >= camera.limit ? "over" : camera.count >= camera.limit * 0.8 ? "near" : "ok";
  const gateLevel = levelOf(gate);
  element.dataset.level = LEVEL_RANK[cameraLevel] > LEVEL_RANK[gateLevel] ? cameraLevel : gateLevel;

  element.querySelector("b").textContent = counting
    ? `${ghat.name} · ${gate.inside.toLocaleString("en-IN")} / ${gate.limit.toLocaleString("en-IN")}`
    : `${ghat.name} · no gate data`;
  element.querySelector("small").textContent = cameraLive ? `CCTV ${camera.count} heads` : "";
}

function addControls(container, { onRecenter, cityName }) {
  const recenter = document.createElement("button");
  recenter.type = "button";
  recenter.textContent = cityName;
  recenter.title = `Zoom back to ${cityName}`;
  recenter.addEventListener("click", onRecenter);

  const traffic = document.createElement("button");
  traffic.type = "button";
  traffic.textContent = "Traffic";

  const satelliteButtons = {
    "2d": makeButton("2D Satellite", "Flat satellite imagery; click again for the dark map"),
    "3d": makeButton("3D Satellite", "Tilted satellite view with terrain and 3D buildings; click again for the dark map"),
  };

  const sim = makeButton("Sim cars", "Also show simulated cars on roads TomTom has no live data for");

  container.append(recenter, traffic, sim, satelliteButtons["2d"], satelliteButtons["3d"]);
  return { trafficButton: traffic, simButton: sim, satelliteButtons };
}

function makeButton(text, title) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.title = title;
  return button;
}

// The two satellite buttons act as a pair: one at a time, click the lit one to go back to the dark map.
function setupSatellite(map, beforeLayerId, buttons) {
  const view = addSatelliteView(map, { beforeLayerId });
  for (const [mode, button] of Object.entries(buttons)) {
    button.addEventListener("click", () => {
      view.setMode(view.getMode() === mode ? "map" : mode);
      for (const [other, otherButton] of Object.entries(buttons)) {
        otherButton.classList.toggle("on", view.getMode() === other);
      }
    });
  }
}

// A short message shown over the map, e.g. why traffic isn't available.
function createMapNotice(container) {
  const element = document.createElement("div");
  element.className = "map-notice";
  element.hidden = true;
  container.append(element);

  let hideTimer;
  return {
    show(text, { autoHideMs = 8000 } = {}) {
      element.textContent = text;
      element.hidden = false;
      clearTimeout(hideTimer);
      if (autoHideMs) hideTimer = setTimeout(() => { element.hidden = true; }, autoHideMs);
    },
  };
}

// Live street traffic (see traffic-layer.js), drawn under the map's labels.
function setupTraffic(map, available, { trafficButton: button, simButton }, notice, beforeLayerId) {
  if (!available) {
    simButton.hidden = true;
    button.classList.add("unavailable");
    button.title = "Live traffic needs a TomTom API key";
    button.addEventListener("click", () =>
      notice.show("Live traffic is off: no TomTom API key. Add TOMTOM_API_KEY=… to the .env file next to README.md and restart the server."),
    );
    return;
  }

  // Say once why traffic tiles stopped coming instead of failing silently.
  const shown = new Set();
  const onError = (status) => {
    const text = TRAFFIC_ERRORS[status];
    if (!text || shown.has(status)) return;
    shown.add(status);
    notice.show(text, { autoHideMs: 0 });
  };

  const traffic = addTrafficLayer(map, { beforeLayerId, onError });

  button.disabled = false;
  button.classList.add("on");
  button.title = "Show or hide live traffic";
  button.addEventListener("click", () => {
    traffic.setVisible(!traffic.isVisible());
    button.classList.toggle("on", traffic.isVisible());
    simButton.disabled = !traffic.isVisible();
  });

  // On by default (fills uncovered roads, like gods-eye-view); off leaves only live-data roads.
  simButton.classList.toggle("on", traffic.isSimulated());
  simButton.addEventListener("click", () => {
    traffic.setSimulated(!traffic.isSimulated());
    simButton.classList.toggle("on", traffic.isSimulated());
  });
}

const TRAFFIC_ERRORS = {
  403: "TomTom rejected the traffic API key. Check the key in the TomTom developer portal.",
  429: "Live traffic paused: today's TomTom tile budget is used up (TOMTOM_DAILY_TILE_BUDGET). Roads already loaded still show.",
  502: "TomTom traffic is not answering right now; retrying in the background.",
  504: "TomTom traffic is not answering right now; retrying in the background.",
};
