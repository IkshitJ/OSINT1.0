"""
Starts the crowd monitor:  python3 -m server

Wires the pieces together: one event hub, the shared alert log and source
status, a background reader per upstream app, and the HTTP server.
"""

import logging

from server import config
from server.alert_log import AlertLog
from server.event_hub import EventHub
from server.feeds.headcounter import HeadcounterFeed
from server.feeds.sensor import SensorFeed
from server.http_app import make_server
from server.source_status import SourceStatus


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    hub = EventHub()
    alerts = AlertLog(hub, max_events=config.ALERT_LOG_SIZE)
    status = SourceStatus(hub)

    SensorFeed(config.SENSOR_URL, config.SENSOR_SITE_NAME, alerts, status).start()
    HeadcounterFeed(
        config.HEADCOUNTER_URL,
        config.HEADCOUNTER_POLL_SECONDS,
        config.HEADCOUNTER_DEFAULT_LIMIT,
        config.HEADCOUNTER_CAMERA_LIMITS,
        config.HEADCOUNTER_HOLD_SECONDS,
        alerts,
        status,
    ).start()

    server = make_server(hub, alerts, status)
    logging.info("Crowd monitor running at http://%s:%d", config.HOST, config.PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
