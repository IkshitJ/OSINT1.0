// Live connection to the dashboard server (/api/events, Server-Sent Events).
//
// On every (re)connect the server first sends a "snapshot" with the whole log
// and source status, so nothing is lost while the connection was down.
// EventSource reconnects by itself.

export function connectLiveEvents({ onSnapshot, onAlert, onSource, onConnectionChange }) {
  const events = new EventSource("/api/events");
  const parse = (handler) => (message) => handler(JSON.parse(message.data));

  events.addEventListener("snapshot", parse(onSnapshot));
  events.addEventListener("alert", parse(onAlert));
  events.addEventListener("source", parse(onSource));
  events.addEventListener("open", () => onConnectionChange(true));
  events.addEventListener("error", () => onConnectionChange(false));

  return events;
}
