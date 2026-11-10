# CCTV live head counter

Made by Ikshit for Calance. Part of the crowd monitor: it runs inside the one server at the top of
this repository (`./run.sh`), one camera per ghat.

- Full page (controls, single-camera view, tripwire and zone editing, charts): `http://localhost:3000/cctv/`
- The dashboard's CCTV panel shows the same four cameras side by side.

```
cctv/
├── config.py               # detection model, sensitivity, tracking, tripwire/zone defaults, colours
├── manager.py              # runs all cameras: one thread, one batched model call per round
├── api.py                  # the /cctv web API: live pictures, counts, uploads, settings
├── cli.py                  # command-line tool: analyse a file / stream, optionally save the result
├── engine/
│   ├── detector.py         # YOLO head detector, MPS (Apple Silicon) / CUDA / CPU
│   ├── tracker.py          # keeps the same head the same ID from frame to frame
│   ├── counter.py          # heads in view, in a zone, and across a tripwire
│   ├── pipeline.py         # one camera: read frames, track, count, draw
│   └── crowd_analyst.py    # optional Claude crowd-risk forecast (POST /cctv/api/analyze)
├── web/index.html          # the full CCTV page
└── docs/                   # technical report, prototype blueprint
```

The model weights live in `models/` at the repository root (downloaded on first use) and each
camera's video in `footage/` (see `footage/README.md`).

## How it runs

One background thread reads a frame from every camera, finds the heads in all of them with a single
model call, then tracks, counts and draws per camera. Four cameras therefore cost the GPU little
more than one. Cameras are analysed 12 times a second while someone is watching and about once a
second otherwise, so the overcrowding log keeps working with every browser closed. Video files play
at their real speed and loop.

Settings (in `.env` or the environment):

| Variable | Default | Meaning |
|---|---|---|
| `CCTV_SOURCE_<GHAT>` | *(the file in `footage/`)* | A camera's source: RTSP URL, file path or webcam number, e.g. `CCTV_SOURCE_RAM_GHAT=rtsp://…` |
| `CCTV_ANALYSIS_FPS` | `12` | Frames analysed per second per camera while watched |
| `CCTV_IDLE_ANALYSIS_FPS` | `1` | The same with nobody watching |
| `CCTV_STREAM_MAX_WIDTH` | `960` | Width of the pictures sent to the browser (detection uses the full frame) |

Detection sensitivity, the tripwire and zone defaults and the drawing colours are in `cctv/config.py`.

## Command line

```bash
.venv/bin/python -m cctv.cli --source footage/ram-ghat.mp4 --save annotated.mp4
.venv/bin/python -m cctv.cli --source rtsp://user:pass@10.0.0.5/stream --show --tripwire
```

## What changed when this moved into the crowd monitor

The Hugging Face / Gradio deployment files, the standalone launch scripts and the sample clips
(Howrah, Kolkata, Shibuya, Paris) were left behind; the cameras are now the four ghats. The original
project, with its history, is the separate `Headcounter` repository.
