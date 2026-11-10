// The CCTV panel: all four ghat cameras, always visible together.
//
// Pictures arrive on one WebSocket for every camera (/cctv/ws): each message
// is a JPEG whose first byte is the camera's position in the list. One socket
// instead of four streams keeps the browser's few connections per site free
// for the map. While the tab is hidden the socket is closed, and the server
// then analyses the cameras only about once a second.

const RECONNECT_MS = 1500;

export function createCctvPanel({ gridEl, totalEl, ghats }) {
  const tiles = ghats.map((ghat) => createTile(ghat));
  gridEl.replaceChildren(...tiles.map((tile) => tile.element));

  let socket = null;
  let reconnectTimer = null;

  function connect() {
    clearTimeout(reconnectTimer);
    if (socket || document.hidden) return;
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/cctv/ws`);
    socket.binaryType = "arraybuffer";
    socket.onmessage = (message) => {
      const tile = tiles[new Uint8Array(message.data, 0, 1)[0]];
      if (tile) tile.showPicture(new Blob([new Uint8Array(message.data, 1)], { type: "image/jpeg" }));
    };
    socket.onclose = () => {
      socket = null;
      if (!document.hidden) reconnectTimer = setTimeout(connect, RECONNECT_MS);
    };
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) socket?.close();
    else connect();
  });
  connect();

  return {
    // cameras: [{ camera_id, ghat_id, name, count, limit, has_video }] or null when CCTV is down
    setReadings(cameras) {
      for (const tile of tiles) tile.setReading(cameras?.find((camera) => camera.ghat_id === tile.ghat.id) ?? null);
      const live = (cameras ?? []).filter((camera) => camera.has_video);
      totalEl.textContent = live.length
        ? `${live.reduce((sum, camera) => sum + camera.count, 0)} heads in view`
        : "";
    },
  };
}

function createTile(ghat) {
  const element = document.createElement("figure");
  element.className = "camera";
  element.dataset.level = "unknown";
  element.innerHTML = `
    <canvas></canvas>
    <figcaption>
      <span class="camera-name"></span>
      <span class="camera-count"></span>
    </figcaption>
    <label class="camera-add" hidden>
      <span>No footage for this ghat yet</span>
      <b>Add a video…</b>
      <input type="file" accept="video/*" hidden>
    </label>`;
  element.querySelector(".camera-name").textContent = ghat.name;

  const canvas = element.querySelector("canvas");
  const context = canvas.getContext("2d");
  const countEl = element.querySelector(".camera-count");
  const addEl = element.querySelector(".camera-add");
  const addText = addEl.querySelector("span");

  // Giving a camera its footage: the server keeps it as footage/<ghat>.<ext>.
  addEl.querySelector("input").addEventListener("change", async (change) => {
    const file = change.target.files[0];
    if (!file) return;
    addText.textContent = `Uploading ${file.name}…`;
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch(`/cctv/api/upload_video?camera_id=${ghat.cameraId}`, { method: "POST", body });
      if (!response.ok) throw new Error((await response.json()).detail ?? `HTTP ${response.status}`);
      addEl.hidden = true;
    } catch (error) {
      addText.textContent = `Could not use that file: ${error.message}`;
    }
    change.target.value = "";
  });

  let decoding = false;
  return {
    ghat,
    element,
    // Decode off the main thread, then draw in one step: no flicker, and a
    // picture that arrives while one is still decoding is simply dropped.
    async showPicture(blob) {
      if (decoding) return;
      decoding = true;
      try {
        const bitmap = await createImageBitmap(blob);
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
        }
        context.drawImage(bitmap, 0, 0);
        bitmap.close();
      } catch {
        // a damaged frame: the next one replaces it
      } finally {
        decoding = false;
      }
    },
    setReading(camera) {
      if (!camera) {
        element.dataset.level = "unknown";
        countEl.textContent = "offline";
        return;
      }
      addEl.hidden = camera.has_video;
      if (!camera.has_video) {
        element.dataset.level = "unknown";
        countEl.textContent = "no video";
        return;
      }
      const { count, limit } = camera;
      element.dataset.level = count >= limit ? "over" : count >= limit * 0.8 ? "near" : "ok";
      countEl.textContent = `${count} / ${limit} heads`;
    },
  };
}
