"""
Head Detector Module
Wraps YOLO object detection fine-tuned on human heads with automated hardware acceleration.
"""

import os
import logging
import threading
import urllib.request
from typing import List, Dict, Any, Optional
import numpy as np

from cctv.config import (
    MODEL_REGISTRY,
    MODELS_DIR,
    DEFAULT_MODEL,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_IOU_THRESHOLD,
    DETECTION_IMAGE_SIZE,
    ACCELERATOR_DEVICE
)

logger = logging.getLogger("HeadDetector")


class HeadDetector:
    """
    Head Detector leveraging YOLO architectures fine-tuned on crowd & head datasets.
    Optimized for high-angle CCTV perspectives (overhead, corridor, partial occlusions).
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        models_dir: str = str(MODELS_DIR),
        conf_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        iou_threshold: float = DEFAULT_IOU_THRESHOLD,
        device: Optional[str] = ACCELERATOR_DEVICE
    ):
        self.model_name = model_name
        self.models_dir = os.path.abspath(models_dir)
        os.makedirs(self.models_dir, exist_ok=True)
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.device = self._resolve_device(device)
        self._lock = threading.Lock()
        self.model = None
        self.model_path = ""
        self.load_model(model_name)

    def _resolve_device(self, user_device: Optional[str]) -> str:
        """Auto-detect optimal hardware acceleration (MPS for Apple Silicon, CUDA for NVIDIA, or CPU)."""
        if user_device:
            return user_device
        try:
            import torch
            if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                logger.info("Apple Silicon GPU (MPS) detected. Accelerating inference with MPS.")
                return "mps"
            elif torch.cuda.is_available():
                logger.info("NVIDIA CUDA detected. Accelerating inference with CUDA.")
                return "cuda"
        except Exception as e:
            logger.warning(f"Error checking accelerator devices: {e}")
        logger.info("Using CPU for inference.")
        return "cpu"

    def _ensure_weights(self, model_key: str) -> str:
        """Ensures the weights file exists locally; downloads it from the registry if missing."""
        if model_key not in MODEL_REGISTRY:
            if os.path.exists(model_key):
                return model_key
            raise ValueError(f"Unknown model '{model_key}'. Available: {list(MODEL_REGISTRY.keys())}")

        info = MODEL_REGISTRY[model_key]
        filename = info["filename"]
        target_path = os.path.join(self.models_dir, filename)

        if os.path.exists(target_path):
            return target_path

        url = info.get("url")
        if url:
            logger.info(f"Downloading head detection weights for '{model_key}' from {url}...")
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
                )
                with urllib.request.urlopen(req) as response, open(target_path, "wb") as out_file:
                    total_length = response.getheader("content-length")
                    total_length = int(total_length) if total_length else None
                    downloaded = 0
                    chunk_size = 1024 * 1024  # 1MB
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        out_file.write(chunk)
                        if total_length:
                            percent = (downloaded / total_length) * 100
                            logger.info(f"Downloading {filename}: {percent:.1f}% ({downloaded / (1024*1024):.1f}MB)")
                logger.info(f"Successfully downloaded {target_path}")
                return target_path
            except Exception as e:
                logger.error(f"Failed to download weights from {url}: {e}")
                if os.path.exists(target_path):
                    os.remove(target_path)
                logger.info("Falling back to standard yolo11n.pt...")
                return os.path.join(self.models_dir, "yolo11n.pt")
        else:
            # Ultralytics downloads its own standard weights to this path on first use.
            return target_path

    def load_model(self, model_name: str):
        """Loads a model into memory."""
        from ultralytics import YOLO

        self.model_name = model_name
        self.model_path = self._ensure_weights(model_name)
        logger.info(f"Loading YOLO model from: {self.model_path} onto device: {self.device}")
        self.model = YOLO(self.model_path)
        logger.info(f"Model {model_name} successfully loaded.")

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Run inference on a single video frame.
        Returns a list of detected heads with coordinates, confidence, and center point.
        """
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        """
        Run inference on several frames in ONE model call (one list of heads per
        frame, in the same order). On a GPU this costs little more than a single
        frame, which is what lets four cameras share one model smoothly.
        """
        if self.model is None:
            raise RuntimeError("Model is not loaded.")
        if not frames:
            return []

        target_classes = MODEL_REGISTRY.get(self.model_name, {}).get("classes", [0])

        with self._lock:
            results = self.model.predict(
                source=frames,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                classes=target_classes,
                imgsz=DETECTION_IMAGE_SIZE,
                device=self.device,
                verbose=False
            )

        all_detections: List[List[Dict[str, Any]]] = []
        for result in results:
            detections: List[Dict[str, Any]] = []
            boxes = result.boxes
            if boxes is not None and len(boxes) > 0:
                # One transfer from the GPU per frame instead of three per head.
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                classes = boxes.cls.cpu().numpy()
                for (x1, y1, x2, y2), conf, cls_id in zip(xyxy.tolist(), confs.tolist(), classes.tolist()):
                    detections.append({
                        "bbox": [x1, y1, x2, y2],
                        "confidence": float(conf),
                        "class_id": int(cls_id),
                        "label": "head",
                        "center": ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                        "width": x2 - x1,
                        "height": y2 - y1
                    })
            all_detections.append(detections)
        return all_detections
