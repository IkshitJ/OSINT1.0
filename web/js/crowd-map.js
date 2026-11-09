// The situation map: opens on India, then flies to Ujjain once loaded.
// Shows a marker per monitored site and, when a TomTom key is configured, live traffic.
//
// `maplibregl` is the global from the MapLibre script tag in index.html.

const START_VIEW = { center: [78.9, 22.6], zoom: 3.6 }; // all of India
const FLY_TO_DELAY_MS = 600;
const FLY_TO_DURATION_MS = 4000;
const NEAR_LIMIT_RATIO = 0.8;

const TRAFFIC_SOURCE_ID = "traffic-flow";
const TRAFFIC_LAYER_ID = "traffic-flow";

export function createCrowdMap({ container, controlsContainer, mapConfig, sites }) {
  const map = new maplibregl.Map({
    container,
    style: mapConfig.styleUrl,
    center: START_VIEW.center,
    zoom: START_VIEW.zoom,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

  const { focusCity } = mapConfig;
  const flyToCity = () =>
    map.flyTo({ center: [focusCity.lon, focusCity.lat], zoom: focusCity.zoom, duration: FLY_TO_DURATION_MS, essential: true });

  const markers = addSiteMarkers(map, sites);
  const trafficButton = addControls(controlsContainer, { onRecenter: flyToCity, cityName: focusCity.name });
  const notice = createMapNotice(container);

  // data-state on the map element: "loading" -> "flying" -> "ready" (at Ujjain, every tile drawn).
  // Lets automated checks (tests/browser_check.py) wait for the real thing instead of guessing.
  container.dataset.state = "loading";
  map.once("load", () => {
    setupTraffic(map, mapConfig.tomtomApiKey, trafficButton, notice);
    setTimeout(() => {
      container.dataset.state = "flying";
      flyToCity();
      map.once("moveend", () => map.once("idle", () => { container.dataset.state = "ready"; }));
    }, FLY_TO_DELAY_MS);
  });

  return {
    // reading: { count, limit, alarm } or null when the site's feed is offline
    setSiteReading(siteId, reading) {
      const marker = markers.get(siteId);
      if (marker) renderSiteMarker(marker.element, marker.site, reading);
    },
    sitesFor(source) {
      return sites.filter((site) => site.source === source).map((site) => site.id);
    },
  };
}

function addSiteMarkers(map, sites) {
  const markers = new Map();
  for (const site of sites) {
    const element = document.createElement("div");
    element.className = "site-marker";
    element.innerHTML = '<span class="site-marker-dot"></span><span class="site-marker-label"></span>';
    renderSiteMarker(element, site, null);
    new maplibregl.Marker({ element, anchor: "top" }).setLngLat([site.lon, site.lat]).addTo(map);
    markers.set(site.id, { element, site });
  }
  return markers;
}

function renderSiteMarker(element, site, reading) {
  const label = element.querySelector(".site-marker-label");
  if (!reading) {
    element.dataset.level = "unknown";
    label.textContent = `${site.name} · no data`;
    return;
  }
  const { count, limit, alarm } = reading;
  element.dataset.level = alarm ? "over" : limit > 0 && count / limit >= NEAR_LIMIT_RATIO ? "near" : "ok";
  label.textContent = `${site.name} · ${count.toLocaleString("en-IN")} / ${limit.toLocaleString("en-IN")}`;
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

  container.append(recenter, traffic);
  return traffic;
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

// Live traffic flow tiles from TomTom, drawn under the map's labels.
// Green = free flow, orange/red = slower, dark red = standstill.
function setupTraffic(map, apiKey, button, notice) {
  if (!apiKey) {
    button.classList.add("unavailable");
    button.title = "Live traffic needs a TomTom API key";
    button.addEventListener("click", () =>
      notice.show("Live traffic is off: no TomTom API key. Add TOMTOM_API_KEY=… to the .env file next to README.md and restart the server."),
    );
    return;
  }

  // TomTom answers 401/403 for a wrong, expired or over-quota key. Say so once instead of failing silently.
  let keyErrorShown = false;
  map.on("error", (event) => {
    const status = event.error?.status;
    if (event.sourceId === TRAFFIC_SOURCE_ID && (status === 401 || status === 403) && !keyErrorShown) {
      keyErrorShown = true;
      notice.show(`TomTom rejected the traffic API key (HTTP ${status}). Check the key and its allowed domains in the TomTom developer portal.`, { autoHideMs: 0 });
    }
  });

  map.addSource(TRAFFIC_SOURCE_ID, {
    type: "raster",
    tiles: [`https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{z}/{x}/{y}.png?key=${encodeURIComponent(apiKey)}&tileSize=256`],
    tileSize: 256,
    attribution: "Traffic © TomTom",
  });
  const firstLabelLayer = map.getStyle().layers.find((layer) => layer.type === "symbol");
  map.addLayer(
    { id: TRAFFIC_LAYER_ID, type: "raster", source: TRAFFIC_SOURCE_ID, paint: { "raster-opacity": 0.85 } },
    firstLabelLayer?.id,
  );

  button.disabled = false;
  button.classList.add("on");
  button.title = "Show or hide live traffic";
  button.addEventListener("click", () => {
    const visible = map.getLayoutProperty(TRAFFIC_LAYER_ID, "visibility") !== "none";
    map.setLayoutProperty(TRAFFIC_LAYER_ID, "visibility", visible ? "none" : "visible");
    button.classList.toggle("on", !visible);
  });
}
