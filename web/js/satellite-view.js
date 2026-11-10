// Satellite views. Modes:
//   "map" (default)  the dark basemap, flat
//   "2d"             satellite imagery, flat, north up
//   "3d"             satellite imagery + terrain relief + extruded buildings, tilted camera
//
// Imagery: Esri World Imagery (free to use with attribution; check Esri's
// terms before commercial use). Terrain: AWS Terrain Tiles (keyless).
// Buildings: OpenFreeMap / OpenStreetMap footprints with their heights.
//
// Esri has imagery down to different zoom levels in different places; past
// that it serves a "Map data not yet available" tile. In satellite modes the
// map asks Esri how deep the imagery goes at the view's centre and won't zoom
// further. Back on the dark map the normal zoom limit returns.

import { ensureOpenFreeMap } from "./openfreemap.js";

const IMAGERY_SOURCE_ID = "satellite-imagery";
const IMAGERY_LAYER_ID = "satellite-imagery";
const TERRAIN_SOURCE_ID = "satellite-terrain";
const BUILDINGS_LAYER_ID = "satellite-buildings";
const TERRAIN_EXAGGERATION = 1.5; // Ujjain is flat; a little extra relief reads as 3D
const PITCH_3D = 60;
const BEARING_3D = -20;
const CAMERA_MS = 1500;

const IMAGERY_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer";
const IMAGERY_MAX_LEVEL = 19;
const IMAGERY_MIN_LEVEL = 10; // every populated place has imagery at least this deep
// MapLibre zoom Z draws 256 px raster tiles from level round(Z + 1), so level L
// stays in use while Z < L - 0.5; stop just before the next level would load.
const ZOOM_FOR_LEVEL = (level) => level - 0.51;

export function addSatelliteView(map, { beforeLayerId }) {
  map.addSource(IMAGERY_SOURCE_ID, {
    type: "raster",
    tiles: [`${IMAGERY_URL}/tile/{z}/{y}/{x}`],
    tileSize: 256,
    maxzoom: IMAGERY_MAX_LEVEL,
    attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  });
  map.addLayer(
    { id: IMAGERY_LAYER_ID, type: "raster", source: IMAGERY_SOURCE_ID, layout: { visibility: "none" } },
    beforeLayerId,
  );

  map.addSource(TERRAIN_SOURCE_ID, {
    type: "raster-dem",
    tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
    encoding: "terrarium",
    tileSize: 256,
    maxzoom: 15,
    attribution: "Terrain: Mapzen, AWS Terrain Tiles",
  });

  map.addLayer(
    {
      id: BUILDINGS_LAYER_ID,
      type: "fill-extrusion",
      source: ensureOpenFreeMap(map),
      "source-layer": "building",
      minzoom: 13,
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": "#d6dde5",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 6],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.75,
      },
    },
    beforeLayerId,
  );

  let mode = "map";
  const normalMaxZoom = map.getMaxZoom();
  const show = (layerId, visible) => map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  // The +/- buttons only refresh on a "zoom" event, which a new limit alone doesn't fire.
  const setMaxZoom = (zoom) => {
    map.setMaxZoom(zoom);
    map.fire("zoom");
  };

  // Cap zoom at the deepest imagery under the view's centre. A newer check
  // (the view moved again) wins over a slower older one.
  let check = 0;
  async function capZoomToImagery() {
    if (mode === "map") return;
    const mine = ++check;
    const { lng, lat } = map.getCenter();
    const level = await deepestImageryLevel(lng, lat);
    if (mine !== check || mode === "map" || level === null) return;
    setMaxZoom(ZOOM_FOR_LEVEL(level)); // also zooms out if already past it
  }
  map.on("moveend", capZoomToImagery);

  return {
    getMode: () => mode,
    setMode(next) {
      mode = next;
      if (mode === "map") {
        check++; // drop any check still in flight
        setMaxZoom(normalMaxZoom);
      } else {
        capZoomToImagery();
      }
      const is3d = mode === "3d";
      show(IMAGERY_LAYER_ID, mode !== "map");
      show(BUILDINGS_LAYER_ID, is3d);
      map.setTerrain(is3d ? { source: TERRAIN_SOURCE_ID, exaggeration: TERRAIN_EXAGGERATION } : null);
      map.easeTo({ pitch: is3d ? PITCH_3D : 0, bearing: is3d ? BEARING_3D : 0, duration: CAMERA_MS });
    },
  };
}

// Esri's tilemap says, per tile, whether real imagery exists (1) or not (0).
const availability = new Map();

async function deepestImageryLevel(lng, lat) {
  for (let level = IMAGERY_MAX_LEVEL; level > IMAGERY_MIN_LEVEL; level--) {
    const { x, y } = tileAt(lng, lat, level);
    const key = `${level}/${y}/${x}`;
    if (!availability.has(key)) {
      try {
        const response = await fetch(`${IMAGERY_URL}/tilemap/${key}/1/1`);
        const body = await response.json();
        availability.set(key, body?.valid === true && body.data?.[0] === 1);
      } catch {
        return null; // can't tell: leave the current limit alone
      }
    }
    if (availability.get(key)) return level;
  }
  return IMAGERY_MIN_LEVEL;
}

function tileAt(lng, lat, level) {
  const n = 2 ** level;
  const latRad = (lat * Math.PI) / 180;
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2) * n),
  };
}
