// Live street traffic on the map, the way gods-eye-view draws it:
//
//  1. TomTom traffic-flow *vector* tiles, fetched through our own server
//     (/api/traffic/flow/...), so the API key never reaches the browser.
//     Roads TomTom covers are colored by traffic_level (current speed /
//     free-flow speed): green free flow, amber slow, red jam, dark red closed.
//  2. Vehicles drive on those live roads at TomTom's live speed, in real time
//     (no speed-up): the road type's normal speed x traffic_level. Jammed
//     roads crawl and pack more vehicles.
//  3. Optional ("Sim cars", off by default): white simulated vehicles on the
//     OpenStreetMap roads TomTom doesn't cover. Each OSM road is matched to
//     the TomTom line it lies on, so covered roads never get simulated cars.
//  4. A spread-out sample of vehicles carries a "VEH-0209" tag with a corner
//     bracket in its congestion color.
//
// What is real: which roads are slow and how fast traffic moves on them.
// What is not: TomTom gives no vehicle positions or counts, so each dot is a
// stand-in placed along the road, more of them where the road is jammed.
//
// Flow tiles refresh every 2 minutes; the server caches them for the same time.

import { ensureOpenFreeMap } from "./openfreemap.js";

const FLOW_SOURCE_ID = "traffic-flow";
const FLOW_SOURCE_LAYER = "Traffic flow"; // layer name inside TomTom's flow tiles
const LINE_LAYER_ID = "traffic-flow-lines";
const ROADS_LAYER_ID = "traffic-osm-roads"; // invisible: only makes MapLibre load road tiles
const DOTS_SOURCE_ID = "traffic-dots";
const DOTS_LAYER_ID = "traffic-dots";
const TAGS_LAYER_ID = "traffic-tags"; // brackets + IDs, from the same source as the dots so they never drift apart
const BRACKET_IMAGE = "veh-bracket";
// A font stack the basemap's glyph server already serves.
const TAG_FONT = ["Montserrat SemiBold", "Open Sans Bold", "Noto Sans Regular", "Noto Sans CJK JP Regular"];
const TILE_URL = "/api/traffic/flow/{z}/{x}/{y}.pbf";
const REFRESH_MS = 120_000;
const REBUILD_DELAY_MS = 300;

// traffic_level thresholds and colors (same as gods-eye-view).
const FREE_LEVEL = 0.85; // at or above: free flow
const SLOW_LEVEL = 0.55; // at or above: slow; below: jam
const COLORS = { free: "#2ecc71", slow: "#f0b23e", jam: "#e05252", closed: "#7f1d1d", simulated: "#e6edf3" };

// OpenStreetMap road classes that carry vehicles: speed (m/s) and how many
// vehicles per kilometre at free flow, per direction (from gods-eye-view).
const ROAD_CLASSES = {
  motorway: { speed: 25, perKm: 9, rank: 0 },
  trunk: { speed: 20, perKm: 7.5, rank: 1 },
  primary: { speed: 14, perKm: 6, rank: 2 },
  secondary: { speed: 11, perKm: 4.5, rank: 3 },
  tertiary: { speed: 8, perKm: 3, rank: 4 },
  minor: { speed: 5, perKm: 1.5, rank: 5 },
};
// Live roads: normal free-flow speed (m/s) and vehicles per km per direction,
// per TomTom road_type.
const TOMTOM_ROAD_TYPES = {
  "Motorway": { speed: 25, perKm: 9 },
  "International road": { speed: 22, perKm: 8 },
  "Major road": { speed: 14, perKm: 6 },
  "Secondary road": { speed: 11, perKm: 4.5 },
  "Connecting road": { speed: 9, perKm: 3.5 },
  "Major local road": { speed: 8, perKm: 3 },
  "Local road": { speed: 6, perKm: 2 },
  "Minor local road": { speed: 5, perKm: 1.5 },
};
const DEFAULT_TOMTOM_ROAD = { speed: 6, perKm: 2 };
const BLOCKED_ACCESS = new Set(["no", "private", "destination", "customers", "delivery", "agricultural", "forestry"]);
const MAX_DOTS = 4000;
// Zoomed out, show a thinner sample of the vehicles (down to 1 in 6): the whole
// city's roads then fit in the dot budget, live and simulated alike, and the
// roads don't turn into solid dotted lines.
const FULL_DENSITY_ZOOM = 14.5;
const MIN_DENSITY = 1 / 6;
// Vehicles move at real speeds, so how often they need redrawing depends on
// the zoom: at city zoom the fastest one covers under 2 px a second, zoomed
// right in it covers hundreds. Redraw often enough that nothing jumps more
// than about half a pixel, between once a second and 30 times a second.
const FASTEST_VEHICLE_MPS = 25;
const MIN_FRAME_MS = 33;
const MAX_FRAME_MS = 1000;

// Flow -> road matching (gods-eye-view's data/flowMatch.js).
const MATCH_CELL_M = 100;
const MATCH_RADIUS_M = 35;
const MATCH_BEARING_DEG = 30;
const MATCH_SAMPLES = 7;
const MATCH_MIN_SAMPLES = 2;

// Vehicle tags: at most one per TAG_CELL_PX square of screen.
const TAG_LIMIT = 36;
const TAG_CELL_PX = 110;

const isClosed = ["any", ["==", ["get", "road_closure"], true], ["==", ["get", "road_closure"], "true"]];
const level = ["coalesce", ["get", "traffic_level"], 1];

export function addTrafficLayer(map, { beforeLayerId, onError }) {
  map.addSource(FLOW_SOURCE_ID, {
    type: "vector",
    tiles: [tileUrl()],
    minzoom: 0,
    maxzoom: 18,
    attribution: "Traffic flow data © TomTom",
  });
  map.addLayer(
    {
      id: LINE_LAYER_ID,
      type: "line",
      source: FLOW_SOURCE_ID,
      "source-layer": FLOW_SOURCE_LAYER,
      // A road without a level can't be colored honestly, unless it's closed.
      filter: ["any", ["has", "traffic_level"], isClosed],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": [
          "case",
          isClosed, COLORS.closed,
          ["<", level, SLOW_LEVEL], COLORS.jam,
          ["<", level, FREE_LEVEL], COLORS.slow,
          COLORS.free,
        ],
        "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 14, 2.5, 17, 5],
        "line-opacity": 0.7,
      },
    },
    beforeLayerId,
  );

  const roadsSourceId = ensureOpenFreeMap(map);
  map.addLayer(
    {
      id: ROADS_LAYER_ID,
      type: "line",
      source: roadsSourceId,
      "source-layer": "transportation",
      filter: ["in", ["get", "class"], ["literal", Object.keys(ROAD_CLASSES)]],
      paint: { "line-opacity": 0 },
    },
    beforeLayerId,
  );

  map.addSource(DOTS_SOURCE_ID, { type: "geojson", data: emptyCollection() });
  map.addLayer(
    {
      id: DOTS_LAYER_ID,
      type: "circle",
      source: DOTS_SOURCE_ID,
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 14, 2.4, 17, 4.5],
        "circle-stroke-color": "#0b0f14",
        "circle-stroke-width": 0.6,
      },
    },
    beforeLayerId,
  );

  map.addImage(BRACKET_IMAGE, bracketImage(), { sdf: true, pixelRatio: 2 });
  map.addLayer(
    {
      id: TAGS_LAYER_ID,
      type: "symbol",
      source: DOTS_SOURCE_ID,
      filter: ["has", "tag"],
      layout: {
        "icon-image": BRACKET_IMAGE,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "text-field": ["get", "tag"],
        "text-font": TAG_FONT,
        "text-size": 10,
        "text-anchor": "bottom-left",
        "text-offset": [0.8, -0.3],
        "text-allow-overlap": true,
        "text-ignore-placement": true,
      },
      paint: {
        "icon-color": ["get", "color"],
        "text-color": ["get", "color"],
        "text-halo-color": "rgba(8, 10, 12, 0.9)",
        "text-halo-width": 1.6,
      },
    },
    beforeLayerId,
  );

  // TomTom (or our proxy) refusing tiles: 403 bad key, 429 daily budget spent, 503 no key.
  map.on("error", (event) => {
    if (event.sourceId === FLOW_SOURCE_ID) onError?.(event.error?.status);
  });

  const vehicles = createVehicles(map, roadsSourceId);
  let visible = true;
  let simulated = true; // fill roads TomTom does not cover, like gods-eye-view

  // Rebuild the vehicles shortly after the view changes or new tiles arrive.
  // (Not on "idle": moving vehicles keep the map busy, so it never goes idle.)
  let rebuildTimer = null;
  const scheduleRebuild = () => {
    if (!visible) return;
    clearTimeout(rebuildTimer);
    rebuildTimer = setTimeout(() => {
      if (visible && map.isSourceLoaded(FLOW_SOURCE_ID)) vehicles.rebuild({ simulated });
      else scheduleRebuild(); // tiles still loading; their "sourcedata" may already have fired
    }, REBUILD_DELAY_MS);
  };
  map.on("moveend", scheduleRebuild);
  map.on("sourcedata", (event) => {
    if ((event.sourceId === FLOW_SOURCE_ID || event.sourceId === roadsSourceId) && event.isSourceLoaded && event.tile) {
      scheduleRebuild();
    }
  });

  // Refetch live flow every 2 minutes while shown.
  setInterval(() => {
    if (!visible || document.hidden) return;
    map.getSource(FLOW_SOURCE_ID)?.setTiles([tileUrl(`?t=${Date.now()}`)]);
  }, REFRESH_MS);

  return {
    setVisible(show) {
      visible = show;
      for (const id of [LINE_LAYER_ID, DOTS_LAYER_ID, TAGS_LAYER_ID]) {
        map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
      }
      if (show) {
        vehicles.start();
        scheduleRebuild();
      } else {
        vehicles.stop();
      }
    },
    isVisible: () => visible,
    setSimulated(show) {
      simulated = show;
      scheduleRebuild();
    },
    isSimulated: () => simulated,
  };
}

// Absolute, because MapLibre loads tiles in a worker. String-built, so {z}/{x}/{y} stay unescaped.
const tileUrl = (query = "") => `${location.origin}${TILE_URL}${query}`;

// --- vehicles --------------------------------------------------------------

function createVehicles(map, roadsSourceId) {
  let dots = [];
  let frameTimer = null;
  let lastTick = 0;

  function rebuild({ simulated }) {
    const flow = readFlow(map);
    const roads = flow.lines; // live: vehicles on TomTom's own road lines

    if (simulated) {
      // Simulated: OSM roads that no TomTom line covers.
      const osmRoads = readRoads(map, roadsSourceId);
      matchFlow(osmRoads, flow);
      for (const road of osmRoads) {
        if (road.flow) continue;
        const cls = ROAD_CLASSES[road.roadClass];
        road.color = COLORS.simulated;
        road.speed = cls.speed;
        road.perKm = cls.perKm;
        road.rank = cls.rank;
        roads.push(road);
      }
    }

    // Live congestion first, then bigger roads, so the dot budget drops
    // quiet side streets before it drops a jam.
    roads.sort((a, b) => (a.flow ? a.flow.level : 2) - (b.flow ? b.flow.level : 2) || (a.rank ?? 0) - (b.rank ?? 0));

    const density = Math.min(1, Math.max(MIN_DENSITY, 2 ** (map.getZoom() - FULL_DENSITY_ZOOM)));
    const next = [];
    for (const road of roads) {
      if (next.length >= MAX_DOTS) break;
      const exact = (road.length / 1000) * road.perKm * density;
      // Short roads still get a vehicle now and then, in proportion to length.
      const count = Math.floor(exact) + (Math.random() < exact % 1 ? 1 : 0);
      for (let i = 0; i < count && next.length < MAX_DOTS; i++) {
        next.push({
          road,
          at: (road.length * (i + Math.random() * 0.6)) / count,
          segment: 0,
          id: vehicleId(road.key, i),
          position: null,
        });
      }
    }
    dots = next;
    draw(); // positions first, so tags can be chosen by where dots are on screen
    chooseTags(map, dots);
    draw();
    start();
  }

  function tick(now) {
    const seconds = Math.min(1.5, (now - lastTick) / 1000); // don't jump after a background tab
    lastTick = now;
    for (const dot of dots) {
      dot.at += dot.road.speed * seconds; // real time
      if (dot.at >= dot.road.length) {
        dot.at %= dot.road.length; // loop back to the road's start
        dot.segment = 0;
      }
    }
    draw();
  }

  function draw() {
    const features = dots.map((dot) => {
      dot.position = positionOf(dot);
      const properties = { color: dot.road.color };
      if (dot.tagged) properties.tag = `VEH-${String(dot.id).padStart(4, "0")}`;
      return { type: "Feature", properties, geometry: { type: "Point", coordinates: dot.position } };
    });
    map.getSource(DOTS_SOURCE_ID)?.setData({ type: "FeatureCollection", features });
  }

  function frameInterval() {
    const { lat } = map.getCenter();
    const metersPerPixel = (78271.5 * Math.cos((lat * Math.PI) / 180)) / 2 ** map.getZoom();
    const msPerHalfPixel = (0.5 * metersPerPixel * 1000) / FASTEST_VEHICLE_MPS;
    return Math.min(MAX_FRAME_MS, Math.max(MIN_FRAME_MS, msPerHalfPixel));
  }

  function start() {
    if (frameTimer || !dots.length) return;
    lastTick = performance.now();
    const frame = () => {
      if (!document.hidden) tick(performance.now());
      else lastTick = performance.now();
      frameTimer = setTimeout(frame, frameInterval());
    };
    frameTimer = setTimeout(frame, frameInterval());
  }

  function stop() {
    clearTimeout(frameTimer);
    frameTimer = null;
  }

  return { rebuild, start, stop };
}

// Public motor roads in view, one entry per travel direction.
function readRoads(map, sourceId) {
  const features = map.querySourceFeatures(sourceId, { sourceLayer: "transportation" });
  const seen = new Set();
  const roads = [];
  for (const feature of features) {
    const p = feature.properties || {};
    if (!ROAD_CLASSES[p.class] || p.subclass || p.service || BLOCKED_ACCESS.has(p.access)) continue;
    if (p.brunnel === "tunnel" || p.indoor) continue;
    for (const coords of lineParts(feature.geometry)) {
      if (coords.length < 2) continue;
      // The same road arrives once per tile it crosses; keep one copy.
      const key = `${coords[0]}|${coords[coords.length - 1]}|${coords.length}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const oneway = Number(p.oneway);
      const directions = oneway === 1 ? [coords] : oneway === -1 ? [coords.slice().reverse()] : [coords, coords.slice().reverse()];
      directions.forEach((line, i) => {
        const road = measure(line);
        if (road.length < 15) return;
        road.key = `${key}|${i}`;
        road.roadClass = p.class;
        roads.push(road);
      });
    }
  }
  return roads;
}

// TomTom flow lines in view: as drivable live roads (one per travel
// direction), and cut into short segments in a spatial grid for matching.
function readFlow(map) {
  const features = map.querySourceFeatures(FLOW_SOURCE_ID, { sourceLayer: FLOW_SOURCE_LAYER });
  const grid = new Map();
  const lines = [];
  const seen = new Set();
  const center = map.getCenter();
  const project = projector(center.lat);
  for (const feature of features) {
    const p = feature.properties || {};
    const closure = p.road_closure === true || p.road_closure === "true";
    const trafficLevel = Number(p.traffic_level);
    if (!Number.isFinite(trafficLevel) && !closure) continue;
    const flow = {
      level: Number.isFinite(trafficLevel) ? Math.min(1, Math.max(0, trafficLevel)) : 0,
      closure,
      bothSides: p.traffic_road_coverage === "full",
    };
    for (const coords of lineParts(feature.geometry)) {
      if (coords.length < 2) continue;
      // The same line arrives once per tile it crosses; keep one copy.
      const key = `${coords[0]}|${coords[coords.length - 1]}|${coords.length}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (!closure) lines.push(...liveRoads(coords, key, flow, p.road_type)); // nothing drives on a closed road
      for (let i = 1; i < coords.length; i++) {
        const [ax, ay] = project(coords[i - 1]);
        const [bx, by] = project(coords[i]);
        const length = Math.hypot(bx - ax, by - ay);
        if (!length) continue;
        const bearing = bearingOf(bx - ax, by - ay);
        // Long pieces are split so each lands in every grid cell it crosses.
        const pieces = Math.ceil(length / MATCH_CELL_M);
        for (let k = 0; k < pieces; k++) {
          const t0 = k / pieces;
          const t1 = (k + 1) / pieces;
          const segment = {
            ax: ax + (bx - ax) * t0, ay: ay + (by - ay) * t0,
            bx: ax + (bx - ax) * t1, by: ay + (by - ay) * t1,
            bearing, flow,
          };
          const cell = cellKey((segment.ax + segment.bx) / 2, (segment.ay + segment.by) / 2);
          if (!grid.has(cell)) grid.set(cell, []);
          grid.get(cell).push(segment);
        }
      }
    }
  }
  return { grid, project, lines };
}

// A "full" coverage line describes both directions of its road; "one_side"
// lines run in their direction of travel.
function liveRoads(coords, key, flow, roadType) {
  const type = TOMTOM_ROAD_TYPES[roadType] || DEFAULT_TOMTOM_ROAD;
  const directions = flow.bothSides ? [coords, coords.slice().reverse()] : [coords];
  return directions
    .map((line, i) => Object.assign(measure(line), {
      key: `live|${key}|${i}`,
      flow,
      color: colorFor(flow.level),
      speed: type.speed * speedScale(flow.level),
      perKm: type.perKm * densityScale(flow.level),
    }))
    .filter((road) => road.length >= 15);
}

// Give each road the TomTom flow it lies on: sample it at a few points, and
// match when at least half the samples sit on a flow line heading the same way.
function matchFlow(roads, { grid, project }) {
  if (!grid.size) return;
  for (const road of roads) {
    road.flow = null;
    const samples = Math.min(MATCH_SAMPLES, Math.max(2, Math.round(road.length / 40)));
    const levels = [];
    let closure = false;
    for (let s = 0; s < samples; s++) {
      const { point, bearing } = pointAlong(road, (road.length * (s + 0.5)) / samples);
      const [px, py] = project(point);
      const hit = nearestFlow(grid, px, py, bearing);
      if (!hit) continue;
      levels.push(hit.level);
      closure ||= hit.closure;
    }
    if (levels.length >= Math.max(MATCH_MIN_SAMPLES, samples / 2)) {
      road.flow = { level: closure ? 0 : median(levels), closure };
    }
  }
}

function nearestFlow(grid, px, py, bearing) {
  const cx = Math.floor(px / MATCH_CELL_M);
  const cy = Math.floor(py / MATCH_CELL_M);
  let best = null;
  let bestDistance = MATCH_RADIUS_M;
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      for (const segment of grid.get(`${cx + dx},${cy + dy}`) || []) {
        const sameWay = angleBetween(bearing, segment.bearing) <= MATCH_BEARING_DEG;
        const otherWay = segment.flow.bothSides && angleBetween(bearing, segment.bearing + 180) <= MATCH_BEARING_DEG;
        if (!sameWay && !otherWay) continue;
        const distance = distanceToSegment(px, py, segment);
        if (distance <= bestDistance) {
          bestDistance = distance;
          best = segment.flow;
        }
      }
    }
  }
  return best;
}

// --- vehicle tags ("VEH-0209" with a corner bracket) -------------------------

// One tag per screen cell, live congestion first, so tags spread over the view.
function chooseTags(map, dots) {
  const candidates = dots
    .filter((dot) => dot.position)
    .sort((a, b) => (a.road.flow ? a.road.flow.level : 2) - (b.road.flow ? b.road.flow.level : 2));
  const used = new Set();
  const { clientWidth: width, clientHeight: height } = map.getContainer();
  let count = 0;
  for (const dot of candidates) {
    dot.tagged = false;
    if (count >= TAG_LIMIT) continue;
    const { x, y } = map.project(dot.position);
    if (x < 0 || y < 0 || x > width || y > height) continue;
    const cell = `${Math.floor(x / TAG_CELL_PX)},${Math.floor(y / TAG_CELL_PX)}`;
    if (used.has(cell)) continue;
    used.add(cell);
    dot.tagged = true;
    count++;
  }
}

// Four corner brackets, drawn white; MapLibre recolors it per vehicle (SDF icon).
function bracketImage() {
  const size = 32; // 16 px on screen at pixelRatio 2
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 3;
  const inset = 4;
  const arm = 8;
  const far = size - inset;
  ctx.beginPath();
  for (const [x, y, dx, dy] of [[inset, inset, 1, 1], [far, inset, -1, 1], [inset, far, 1, -1], [far, far, -1, -1]]) {
    ctx.moveTo(x + dx * arm, y);
    ctx.lineTo(x, y);
    ctx.lineTo(x, y + dy * arm);
  }
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

// Same road, same vehicle number after a rebuild, so tags don't reshuffle.
function vehicleId(roadKey, index) {
  let hash = 0;
  for (let i = 0; i < roadKey.length; i++) hash = (hash * 31 + roadKey.charCodeAt(i)) | 0;
  return 1 + ((Math.abs(hash) + index * 7919) % 9999);
}

// --- geometry ----------------------------------------------------------------

function lineParts(geometry) {
  if (geometry.type === "LineString") return [geometry.coordinates];
  if (geometry.type === "MultiLineString") return geometry.coordinates;
  return [];
}

// Cumulative distance (m) at every vertex, so a dot can be placed by distance.
function measure(coords) {
  const distances = [0];
  for (let i = 1; i < coords.length; i++) {
    distances.push(distances[i - 1] + metersBetween(coords[i - 1], coords[i]));
  }
  return { coords, distances, length: distances[distances.length - 1] };
}

function positionOf(dot) {
  const { coords, distances } = dot.road;
  while (dot.segment < distances.length - 2 && distances[dot.segment + 1] < dot.at) dot.segment++;
  return interpolate(coords, distances, dot.segment, dot.at);
}

function pointAlong(road, at) {
  const { coords, distances } = road;
  let i = 0;
  while (i < distances.length - 2 && distances[i + 1] < at) i++;
  const [ax, ay] = coords[i];
  const [bx, by] = coords[i + 1];
  const bearing = bearingOf((bx - ax) * Math.cos((ay * Math.PI) / 180), by - ay);
  return { point: interpolate(coords, distances, i, at), bearing };
}

function interpolate(coords, distances, i, at) {
  const span = distances[i + 1] - distances[i] || 1;
  const t = Math.min(1, Math.max(0, (at - distances[i]) / span));
  return [coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t, coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t];
}

function metersBetween([lon1, lat1], [lon2, lat2]) {
  const x = ((lon2 - lon1) * Math.PI / 180) * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = ((lat2 - lat1) * Math.PI) / 180;
  return Math.hypot(x, y) * 6371000;
}

// Local flat projection to metres, good for a city-sized view.
function projector(refLat) {
  const perDegLat = 111320;
  const perDegLon = perDegLat * Math.cos((refLat * Math.PI) / 180);
  return ([lon, lat]) => [lon * perDegLon, lat * perDegLat];
}

const cellKey = (x, y) => `${Math.floor(x / MATCH_CELL_M)},${Math.floor(y / MATCH_CELL_M)}`;
const bearingOf = (dx, dy) => (Math.atan2(dx, dy) * 180) / Math.PI;

function angleBetween(a, b) {
  const d = Math.abs((((a - b) % 360) + 540) % 360 - 180);
  return d;
}

function distanceToSegment(px, py, { ax, ay, bx, by }) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function colorFor(trafficLevel) {
  if (trafficLevel >= FREE_LEVEL) return COLORS.free;
  if (trafficLevel >= SLOW_LEVEL) return COLORS.slow;
  return COLORS.jam;
}

// Jammed roads crawl but never freeze, so the layer always looks alive.
const speedScale = (trafficLevel) => Math.min(1, Math.max(0.15, trafficLevel));
// Congestion means more cars on the road: slower roads pack more dots.
const densityScale = (trafficLevel) => Math.min(2.5, 1 / Math.max(trafficLevel, 0.4));

const emptyCollection = () => ({ type: "FeatureCollection", features: [] });
