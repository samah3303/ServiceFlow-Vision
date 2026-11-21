"""Evidence-backed persistence for customers seated in fixed-camera views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .geometry import bbox_iou


def _patch(frame: np.ndarray, box: List[float]) -> Optional[np.ndarray]:
    height, width = frame.shape[:2]
    x, y, w, h = box
    left, top = max(0, int(x * width)), max(0, int(y * height))
    right, bottom = min(width, int((x + w) * width)), min(height, int((y + h) * height))
    if right - left < 12 or bottom - top < 16:
        return None
    crop = cv2.resize(frame[top:bottom, left:right], (48, 64), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(crop, (5, 5), 0)


def _same_appearance(reference: np.ndarray, current: np.ndarray) -> bool:
    difference = float(np.mean(cv2.absdiff(reference, current))) / 255.0
    grayscale_a = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    grayscale_b = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    correlation = float(cv2.matchTemplate(grayscale_a, grayscale_b, cv2.TM_CCOEFF_NORMED)[0, 0])
    return difference < .075 or (difference < .13 and correlation > .72)


@dataclass
class SeatAnchor:
    identity: str
    table_id: str
    camera_id: str
    bbox: List[float]
    map_point: Optional[List[float]]
    zone_ids: List[str]
    patch: np.ndarray
    last_detected: float
    first_detected: float
    hits: int = 1
    absent_frames: int = 0
    confidence: float = 0.0


class SeatedPresence:
    """Hold a seat through missed detections, but retire it on visual departure.

    A seat is anchored after repeated detections at one table. Predicted rows
    are explicitly marked as estimated and never treated as new face evidence.
    """

    def __init__(self, camera_id: str, confirmation_seconds: float = .5,
                 vacancy_frames: int = 3) -> None:
        self.camera_id = camera_id
        self.confirmation_seconds = confirmation_seconds
        self.vacancy_frames = vacancy_frames
        self.anchors: Dict[str, SeatAnchor] = {}

    def update(self, frame: np.ndarray, rows: List[Dict], timestamp: float) -> List[Dict]:
        detected = [row for row in rows if row.get("role") == "customer" and row.get("tableId")
                    and row.get("source") != "seated-persistence"]
        seen = set()
        for row in detected:
            identity = row["globalId"]
            if identity.startswith("pending:"):
                continue
            patch = _patch(frame, row["bbox"])
            if patch is None:
                continue
            anchor = self.anchors.get(identity)
            if anchor and anchor.table_id != row["tableId"]:
                del self.anchors[identity]
                anchor = None
            if anchor is None:
                self.anchors[identity] = SeatAnchor(
                    identity, row["tableId"], self.camera_id, list(row["bbox"]),
                    row.get("map"), list(row.get("zoneIds", [])), patch, timestamp, timestamp,
                    confidence=float(row.get("confidence") or 0),
                )
            else:
                anchor.hits += 1
                anchor.bbox = list(row["bbox"])
                anchor.map_point = row.get("map")
                anchor.zone_ids = list(row.get("zoneIds", []))
                anchor.patch = patch
                anchor.last_detected = timestamp
                anchor.absent_frames = 0
                anchor.confidence = float(row.get("confidence") or 0)
            seen.add(identity)

        # A tracked customer observed walking away is positive departure
        # evidence; do not keep their former seat occupied.
        for row in rows:
            identity = row.get("globalId")
            anchor = self.anchors.get(identity)
            if anchor and row.get("source") != "seated-persistence" and row.get("tableId") != anchor.table_id:
                del self.anchors[identity]

        estimated = []
        for identity, anchor in list(self.anchors.items()):
            if identity in seen or identity not in self.anchors:
                continue
            if anchor.hits < 2 or anchor.last_detected - anchor.first_detected < self.confirmation_seconds:
                # A lone detection is not enough to claim enduring occupancy.
                if timestamp - anchor.last_detected > 2:
                    del self.anchors[identity]
                continue
            if any(row.get("role") == "customer" and row.get("globalId") != identity
                   and bbox_iou(row["bbox"], anchor.bbox) > .35 for row in rows):
                del self.anchors[identity]
                continue
            current = _patch(frame, anchor.bbox)
            if current is None:
                del self.anchors[identity]
                continue
            occluded = any(row.get("role") != "customer" and bbox_iou(row["bbox"], anchor.bbox) > .35
                           for row in rows)
            if not occluded and not _same_appearance(anchor.patch, current):
                anchor.absent_frames += 1
                if anchor.absent_frames >= self.vacancy_frames:
                    del self.anchors[identity]
                    continue
            else:
                anchor.absent_frames = 0
            estimated.append({
                "cameraId": self.camera_id, "localId": f"seat:{identity}",
                "time": round(timestamp, 3), "globalId": identity, "role": "customer",
                "bbox": list(anchor.bbox), "map": anchor.map_point,
                "tableId": anchor.table_id, "zoneIds": list(anchor.zone_ids),
                "confidence": round(min(anchor.confidence, .3), 4), "identityConfidence": 0.0,
                "source": "seated-persistence", "estimated": True,
                "needsReview": "possible-seat-departure" if anchor.absent_frames else None,
            })
        return estimated
