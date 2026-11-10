"""
The CCTV part of the crowd monitor's web API, mounted under /cctv:

    GET  /cctv/                      the full CCTV page (controls, charts, single-camera view)
    WS   /cctv/ws                    live pictures of every camera over one connection
    GET  /cctv/api/status            live counts per camera
    GET  /cctv/api/frame/{cam}       the camera's newest picture (JPEG)
    GET  /cctv/api/stream/{cam}      the camera as an MJPEG stream
    POST /cctv/api/upload_video      give a camera its footage (kept in footage/)
    POST /cctv/api/set_source        point a camera at a file, webcam or RTSP stream
    POST /cctv/api/settings|tripwire|roi|reset_counts, GET /cctv/api/history|export_csv
    POST /cctv/api/analyze           optional Claude crowd-risk forecast

Each picture on the WebSocket is one binary message: the first byte is the
camera's position in the camera list (0-3), the rest is the JPEG. One socket
for all cameras keeps the browser's few per-site connections free.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from cctv.config import (
    DEFAULT_ROI_POLYGON,
    DEFAULT_TRIPWIRE_LINE,
    FOOTAGE_DIR,
    VIDEO_EXTENSIONS,
    WEB_DIR,
)
from cctv.manager import CameraManager

log = logging.getLogger("cctv")

NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
WS_POLL_SECONDS = 0.02  # how often the socket checks for new pictures (they arrive ~12 times a second)


class SourceRequest(BaseModel):
    camera_id: Optional[str] = "cam1"
    source_type: str  # 'webcam', 'rtsp', 'file', 'none'
    url: Optional[str] = None
    index: Optional[int] = 0
    filepath: Optional[str] = None


class SettingsRequest(BaseModel):
    camera_id: Optional[str] = None
    model_name: Optional[str] = None
    conf_threshold: Optional[float] = None
    is_paused: Optional[bool] = None
    overlay_type: Optional[str] = None
    enabled: Optional[bool] = None


class TripwireRequest(BaseModel):
    camera_id: Optional[str] = "cam1"
    enabled: bool
    pt1: Optional[List[float]] = None
    pt2: Optional[List[float]] = None


class ROIRequest(BaseModel):
    camera_id: Optional[str] = "cam1"
    enabled: bool
    points: Optional[List[List[float]]] = None


class AnalyzeRequest(BaseModel):
    camera_id: Optional[str] = "cam1"
    zone_name: Optional[str] = None
    area_m2: Optional[float] = None
    topology: Optional[str] = None
    operator_notes: Optional[str] = None
    extra_telemetry: Optional[Dict[str, Any]] = None


def create_router(get_manager: Callable[[], CameraManager]) -> APIRouter:
    """`get_manager` returns the running CameraManager (it is created when the server starts)."""
    router = APIRouter()
    last_analysis: Dict[str, Any] = {}

    def pipeline_or_404(camera_id: Optional[str]):
        pipeline = get_manager().pipelines.get(camera_id or "cam1")
        if pipeline is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        return pipeline

    # --- pages and pictures -------------------------------------------------------

    @router.get("/", response_class=FileResponse)
    def cctv_page():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @router.websocket("/ws")
    async def live_pictures(socket: WebSocket):
        await socket.accept()
        manager = get_manager()
        seen: Dict[str, int] = {}
        try:
            with manager.watching():
                while True:
                    for camera_id, sequence, jpeg in manager.frames_after(seen):
                        seen[camera_id] = sequence
                        await socket.send_bytes(bytes([manager.camera_ids.index(camera_id)]) + jpeg)
                    await asyncio.sleep(WS_POLL_SECONDS)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass  # the browser went away

    def mjpeg(camera_id: str):
        manager = get_manager()
        seen = -1
        with manager.watching():
            while True:
                seen, jpeg = manager.wait_for_frame(camera_id, seen)
                if jpeg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

    @router.get("/api/stream/{camera_id}")
    def camera_stream(camera_id: str):
        pipeline_or_404(camera_id)
        return StreamingResponse(
            mjpeg(camera_id),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={**NO_CACHE, "X-Accel-Buffering": "no"},
        )

    @router.get("/api/frame/{camera_id}")
    def camera_frame(camera_id: str):
        pipeline_or_404(camera_id)
        return Response(content=get_manager().latest_frame(camera_id), media_type="image/jpeg", headers=NO_CACHE)

    # --- numbers --------------------------------------------------------------------

    @router.get("/api/status")
    def status() -> Dict[str, Any]:
        return get_manager().status()

    @router.get("/api/history")
    def history(cam_id: str = "cam1") -> List[Dict[str, Any]]:
        return pipeline_or_404(cam_id).counter.history

    @router.get("/api/export_csv")
    def export_csv(cam_id: str = "cam1"):
        pipeline = pipeline_or_404(cam_id)
        output = io.StringIO()
        output.write(f"# Camera: {pipeline.camera_name}\n")
        output.write("timestamp,datetime,head_count,roi_count,in_count,out_count\n")
        for entry in pipeline.counter.history:
            ts = entry["time"]
            dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            output.write(f"{ts},{dt_str},{entry['count']},{entry['roi_count']},{entry['in']},{entry['out']}\n")
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=cctv_{cam_id}_headcount_{int(time.time())}.csv"},
        )

    # --- changing a camera -------------------------------------------------------------

    @router.post("/api/upload_video")
    def upload_video(file: UploadFile = File(...), camera_id: str = "cam1"):
        """
        Saves the video as this camera's footage (footage/<ghat>.<ext>, replacing
        any earlier one) and switches the camera to it, so it is still there
        after a restart.
        """
        pipeline_or_404(camera_id)
        manager = get_manager()
        extension = os.path.splitext(file.filename or "")[1].lower()
        if extension not in VIDEO_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Not a video file. Use one of: {', '.join(VIDEO_EXTENSIONS)}")

        slot = manager.cameras[camera_id].get("ghat_id") or camera_id
        destination = FOOTAGE_DIR / f"{slot}{extension}"
        incoming = FOOTAGE_DIR / f".incoming-{slot}{extension}"
        with open(incoming, "wb") as out:  # copy first, so the cameras keep running during a big upload
            shutil.copyfileobj(file.file, out)
        with manager.lock:
            manager.pipelines[camera_id].open_source(None)  # let go of the old file before replacing it
            for old in FOOTAGE_DIR.glob(f"{slot}.*"):
                if old.suffix.lower() in VIDEO_EXTENSIONS:
                    old.unlink()
            incoming.replace(destination)
            opened = manager.set_source(camera_id, str(destination))
        if not opened:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="That file could not be read as a video.")
        log.info("Camera %s now plays %s", camera_id, destination)
        return {"success": True, "camera_id": camera_id, "saved_path": str(destination)}

    @router.post("/api/set_source")
    def set_source(req: SourceRequest):
        camera_id = req.camera_id or "cam1"
        pipeline_or_404(camera_id)
        if req.source_type == "webcam":
            source: Any = int(req.index or 0)
        elif req.source_type == "rtsp":
            if not req.url:
                raise HTTPException(status_code=400, detail="RTSP URL required")
            source = req.url
        elif req.source_type == "file":
            if not req.filepath or not os.path.exists(req.filepath):
                raise HTTPException(status_code=404, detail="File not found")
            source = req.filepath
        elif req.source_type == "none":  # take the camera's video away
            get_manager().set_source(camera_id, None)
            return {"success": True, "camera_id": camera_id}
        else:
            raise HTTPException(status_code=400, detail="Unknown source_type")
        return {"success": get_manager().set_source(camera_id, source), "camera_id": camera_id}

    @router.post("/api/settings")
    def update_settings(req: SettingsRequest):
        manager = get_manager()
        with manager.lock:
            if req.model_name:
                manager.detector.load_model(req.model_name)
                for pipeline in manager.pipelines.values():
                    pipeline.tracker.reset()
            targets = [pipeline_or_404(req.camera_id)] if req.camera_id else list(manager.pipelines.values())
            for pipeline in targets:
                if req.conf_threshold is not None:
                    pipeline.set_confidence(req.conf_threshold)
                if req.is_paused is not None:
                    pipeline.is_paused = req.is_paused
                if req.overlay_type and req.enabled is not None:
                    if req.overlay_type == "boxes":
                        pipeline.show_boxes = req.enabled
                    elif req.overlay_type == "trails":
                        pipeline.show_trails = req.enabled
                    elif req.overlay_type == "crosshairs":
                        pipeline.show_crosshairs = req.enabled
                    elif req.overlay_type == "hud":
                        pipeline.show_hud = req.enabled
        return {"success": True}

    @router.post("/api/tripwire")
    def configure_tripwire(req: TripwireRequest):
        pipeline = pipeline_or_404(req.camera_id)
        with get_manager().lock:
            if req.enabled:
                pt1 = tuple(req.pt1) if req.pt1 is not None else DEFAULT_TRIPWIRE_LINE[0]
                pt2 = tuple(req.pt2) if req.pt2 is not None else DEFAULT_TRIPWIRE_LINE[1]
                pipeline.set_counting_line(pt1, pt2)
            else:
                pipeline.set_counting_line(None, None)
        return {
            "success": True,
            "camera_id": req.camera_id or "cam1",
            "tripwire_enabled": req.enabled,
            "pt1": pipeline.raw_counting_line[0] if pipeline.raw_counting_line else None,
            "pt2": pipeline.raw_counting_line[1] if pipeline.raw_counting_line else None,
        }

    @router.post("/api/roi")
    def configure_roi(req: ROIRequest):
        pipeline = pipeline_or_404(req.camera_id)
        with get_manager().lock:
            if req.enabled:
                points = [tuple(p) for p in req.points] if req.points is not None else DEFAULT_ROI_POLYGON
                pipeline.set_roi_polygon(points)
            else:
                pipeline.set_roi_polygon(None)
        return {
            "success": True,
            "camera_id": req.camera_id or "cam1",
            "roi_enabled": req.enabled,
            "points": pipeline.raw_roi_polygon if pipeline.raw_roi_polygon else None,
        }

    @router.post("/api/reset_counts")
    def reset_counts(camera_id: Optional[str] = None):
        manager = get_manager()
        with manager.lock:
            targets = [pipeline_or_404(camera_id)] if camera_id else list(manager.pipelines.values())
            for pipeline in targets:
                pipeline.counter.reset()
                pipeline.tracker.reset()
        return {"success": True}

    # --- optional Claude crowd analysis ---------------------------------------------------

    @router.post("/api/analyze")
    def run_crowd_analysis(req: AnalyzeRequest):
        """Fuses live counts with GTFS-RT and returns a 20-minute stampede-risk forecast from Claude."""
        import anthropic
        from cctv.engine import crowd_analyst

        pipeline = pipeline_or_404(req.camera_id)
        try:
            result = crowd_analyst.analyze(
                pipeline,
                zone_name=req.zone_name or crowd_analyst.ZONE_NAME,
                area_m2=req.area_m2 or crowd_analyst.ZONE_AREA_M2,
                topology=req.topology or crowd_analyst.ZONE_TOPOLOGY,
                operator_notes=req.operator_notes,
                extra_telemetry=req.extra_telemetry,
            )
        except (anthropic.AuthenticationError, TypeError):
            raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is missing or invalid")
        except anthropic.RateLimitError:
            raise HTTPException(status_code=429, detail="Claude API rate limit hit; retry shortly")
        except anthropic.APIStatusError as e:
            raise HTTPException(status_code=502, detail=f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise HTTPException(status_code=502, detail="Could not reach the Claude API")
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

        last_analysis.clear()
        last_analysis.update(result)
        return result

    @router.get("/api/analyze/last")
    def get_last_analysis():
        if not last_analysis:
            raise HTTPException(status_code=404, detail="No analysis has been run yet")
        return last_analysis

    return router
