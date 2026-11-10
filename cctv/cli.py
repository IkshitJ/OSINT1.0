#!/usr/bin/env python3
"""
CCTV AI Head Counter CLI Tool
Run live head detection & counting on RTSP streams, video files, or webcams.

    .venv/bin/python -m cctv.cli --source footage/ram-ghat.webm --save out.mp4
"""

import argparse
import sys
import os
import cv2
import time

from cctv.config import (
    DEFAULT_MODEL,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_TRIPWIRE_LINE,
    MODEL_REGISTRY
)
from cctv.engine.pipeline import VideoPipeline


def main():
    parser = argparse.ArgumentParser(
        description="CCTV Live Head Counter - High-Angle CCTV Surveillance Head Detection & Live Count"
    )
    parser.add_argument(
        "--source", "-s",
        required=True,
        help="Video source: RTSP stream URL (rtsp://...), webcam index (0, 1), or video file (.mp4)"
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        choices=list(MODEL_REGISTRY.keys()),
        help="AI Head detection model"
    )
    parser.add_argument(
        "--conf", "-c",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help="Confidence threshold (0.10 - 0.90)"
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to save annotated output video (.mp4)"
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display OpenCV live window playback"
    )
    parser.add_argument(
        "--tripwire",
        action="store_true",
        help="Enable tripwire counting line across the center"
    )

    args = parser.parse_args()

    # Determine source type
    src = args.source
    if str(src).isdigit():
        src = int(src)

    print("=" * 65)
    print("  📹 CCTV LIVE HEAD COUNTER")
    print("=" * 65)
    print(f" Source:       {args.source}")
    print(f" Model:        {args.model}")
    print(f" Confidence:   {args.conf}")
    print(f" Tripwire:     {args.tripwire}")
    print(f" Output Video: {args.save or 'None (Display / Live Stream)'}")
    print("=" * 65)

    pipeline = VideoPipeline(
        source=src,
        model_name=args.model,
        conf_threshold=args.conf
    )

    if not pipeline.open_source(src):
        print(f"[-] Error: Could not open video source '{src}'")
        sys.exit(1)

    if args.tripwire:
        pipeline.set_counting_line(DEFAULT_TRIPWIRE_LINE[0], DEFAULT_TRIPWIRE_LINE[1])

    # If saving offline video without live GUI
    if args.save and not args.show:
        print(f"[*] Processing video file to {args.save}...")
        start_t = time.time()
        def on_prog(curr, tot):
            pct = (curr / tot) * 100 if tot > 0 else 0
            print(f"\rProcessing frame {curr}/{tot} ({pct:.1f}%)", end="")
        res = pipeline.process_video_file(src, args.save, progress_callback=on_prog)
        print()
        elapsed = time.time() - start_t
        print(f"[+] Complete in {elapsed:.1f}s!")
        print(f"    Peak Head Count:     {res['peak_head_count']}")
        print(f"    Total Unique Heads:  {res['total_unique_heads']}")
        print(f"    Saved to:            {res['output_path']}")
        sys.exit(0)

    # Live frame loop
    print("[*] Starting live head count loop... Press 'q' in video window or Ctrl+C to stop.")
    frame_count = 0
    writer = None

    try:
        while True:
            ret, frame = pipeline.cap.read()
            if not ret:
                if isinstance(src, str) and os.path.exists(src):
                    pipeline.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                else:
                    break

            annotated_frame, counts = pipeline.process_frame(frame)
            frame_count += 1

            if args.save:
                if writer is None:
                    h, w = annotated_frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(args.save, fourcc, 25.0, (w, h))
                writer.write(annotated_frame)

            # Print live console headcount status every 15 frames
            if frame_count % 15 == 0:
                print(
                    f"\r[LIVE] Heads: {counts['current_head_count']} | "
                    f"Peak: {counts['peak_head_count']} | "
                    f"In: {counts['in_count']} | Out: {counts['out_count']} | "
                    f"FPS: {counts['fps']} | Latency: {counts['latency_ms']} ms",
                    end="",
                    flush=True
                )

            if args.show:
                cv2.imshow("CCTV Live Head Counter", annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:  # Esc or Q
                    break

    except KeyboardInterrupt:
        print("\n[*] Stopped by user.")
    finally:
        pipeline.close()
        if writer:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()
        print("\n[+] Closed CCTV pipeline.")


if __name__ == "__main__":
    main()
