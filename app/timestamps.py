"""Read burned-in camera clocks and align local video timelines conservatively."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

import cv2

try:
    from rapidocr import RapidOCR
except ImportError:
    RapidOCR = None


STAMP = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s+([01]?\d|2[0-3]):([0-5]\d):([0-5]\d)")


def parse_timestamp(lines: List[str]) -> Optional[datetime]:
    text = " ".join(lines).replace("_", "-")
    match = STAMP.search(text)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()))
    except ValueError:
        return None


def read_camera_clock(root: Path, camera: Dict, ocr) -> Dict:
    path = root / camera.get("videoPath", "")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"status": "unreadable", "reason": "Could not open recording"}
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or camera.get("metadata", {}).get("fps") or 25)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = max(0.0, (frames - 1) / fps) if frames else float(camera.get("metadata", {}).get("durationSeconds") or 0)
        sample_seconds = sorted({0.0, min(30.0, duration / 3), min(90.0, duration * 2 / 3)})
        starts: List[datetime] = []
        samples = []
        for seconds in sample_seconds:
            capture.set(cv2.CAP_PROP_POS_FRAMES, round(seconds * fps))
            ok, frame = capture.read()
            if not ok:
                continue
            local_time = max(0.0, (float(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1) / fps)
            height, width = frame.shape[:2]
            top = frame[: max(1, round(height * .14))]
            crops = [top[:, round(width * .55):], top[:, :round(width * .45)]]
            stamp = None
            for crop in crops:
                result = ocr(crop)
                stamp = parse_timestamp(list(result.txts or []))
                if stamp:
                    break
            if stamp:
                starts.append(stamp - timedelta(seconds=local_time))
                samples.append({"localSeconds": round(local_time, 2), "overlay": stamp.isoformat(sep=" ")})
        if len(starts) < 2:
            return {"status": "insufficient", "samples": samples,
                    "reason": "Fewer than two valid clock readings"}
        spread = (max(starts) - min(starts)).total_seconds()
        if spread > 2.0:
            return {"status": "inconsistent", "samples": samples,
                    "spreadSeconds": round(spread, 2), "reason": "Clock readings disagree"}
        epoch = datetime(2000, 1, 1)
        median_seconds = median((start - epoch).total_seconds() for start in starts)
        inferred_start = epoch + timedelta(seconds=median_seconds)
        return {"status": "verified", "startTime": inferred_start.isoformat(sep=" "),
                "samples": samples, "spreadSeconds": round(spread, 2)}
    finally:
        capture.release()


def synchronize_project_cameras(root: Path, project: Dict) -> Dict:
    cameras = [camera for camera in project.get("cameras", []) if camera.get("videoPath")]
    if len(cameras) < 2:
        return {"status": "single-camera", "applied": False, "cameras": []}
    if RapidOCR is None:
        return {"status": "ocr-unavailable", "applied": False, "cameras": [],
                "reason": "RapidOCR is not installed; existing manual offsets were retained"}
    try:
        ocr = RapidOCR()
    except Exception as error:
        logging.warning("Timestamp OCR unavailable: %s", error)
        return {"status": "ocr-unavailable", "applied": False, "cameras": [],
                "reason": "Could not initialize local OCR; existing offsets were retained"}

    readings = {}
    for camera in cameras:
        try:
            readings[camera["id"]] = read_camera_clock(root, camera, ocr)
        except Exception as error:
            logging.warning("Could not read timestamp on %s: %s", camera.get("name"), error)
            readings[camera["id"]] = {"status": "unreadable", "reason": "OCR failed"}
    reference = next((camera for camera in cameras if readings[camera["id"]]["status"] == "verified"), None)
    if reference is None:
        return {"status": "insufficient", "applied": False, "cameras": [
            {"cameraId": camera["id"], **readings[camera["id"]]} for camera in cameras]}
    reference_start = datetime.fromisoformat(readings[reference["id"]]["startTime"])
    reference_offset = (float(reference.get("timeOffsetSeconds", 0))
                        - float(reference.get("manualTimeAdjustmentSeconds", 0)))
    applied = False
    rows = []
    for camera in cameras:
        reading = readings[camera["id"]]
        row = {"cameraId": camera["id"], **reading}
        if reading["status"] == "verified":
            camera_start = datetime.fromisoformat(reading["startTime"])
            proposed = round(reference_offset + (camera_start - reference_start).total_seconds()
                             + float(camera.get("manualTimeAdjustmentSeconds", 0)), 2)
            previous = float(camera.get("timeOffsetSeconds", 0))
            row.update({"previousOffsetSeconds": previous, "offsetSeconds": proposed,
                        "referenceCameraId": reference["id"]})
            camera["timeOffsetSeconds"] = proposed
            camera["timestampSync"] = {"status": "verified", "startTime": reading["startTime"],
                                       "offsetSeconds": proposed, "samples": len(reading["samples"]),
                                       "spreadSeconds": reading["spreadSeconds"]}
            applied = applied or abs(proposed - previous) >= .1
        else:
            camera["timestampSync"] = {"status": reading["status"],
                                       "reason": reading.get("reason", "Could not verify overlay")}
        rows.append(row)
    return {"status": "verified", "applied": applied, "referenceCameraId": reference["id"],
            "cameras": rows}
