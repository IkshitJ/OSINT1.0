// OpenFreeMap: free, keyless OpenStreetMap vector tiles (roads, buildings).
// Shared by the traffic layer (roads for vehicles) and the 3D view (buildings).

export const OPENFREEMAP_SOURCE_ID = "openfreemap";

export function ensureOpenFreeMap(map) {
  if (!map.getSource(OPENFREEMAP_SOURCE_ID)) {
    map.addSource(OPENFREEMAP_SOURCE_ID, {
      type: "vector",
      url: "https://tiles.openfreemap.org/planet",
      attribution: '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © OpenMapTiles',
    });
  }
  return OPENFREEMAP_SOURCE_ID;
}
