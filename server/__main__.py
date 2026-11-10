"""
Starts the crowd monitor:  ./run.sh   (or  .venv/bin/python -m server)

One process serves the dashboard, the sensor-gate simulation of every ghat
and the CCTV head counting. See server/app.py for the addresses.
"""

import logging

import uvicorn

from server import config
from server.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Request-by-request access logs would drown the feed messages; put a reverse proxy in front for those.
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
