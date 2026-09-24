from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np


Point = Tuple[float, float]


def homography_from_calibration(
    calibration: Sequence[Dict[str, Sequence[float]]],
    frame_width: int,
    frame_height: int,
) -> Optional[np.ndarray]:
    """Build an image-to-layout homography from normalized point pairs."""
    if len(calibration) < 4:
        return None
    image_points = np.asarray(
        [
            [pair["image"][0] * frame_width, pair["image"][1] * frame_height]
            for pair in calibration
        ],
        dtype=np.float32,
    )
    map_points = np.asarray([pair["map"] for pair in calibration], dtype=np.float32)
    matrix, _ = cv2.findHomography(image_points, map_points, cv2.RANSAC, 0.025)
    return matrix


def project_point(point: Point, homography: Optional[np.ndarray]) -> Optional[Point]:
    if homography is None:
        return None
    source = np.asarray([[[point[0], point[1]]]], dtype=np.float32)
    destination = cv2.perspectiveTransform(source, homography)[0][0]
    if not np.isfinite(destination).all():
        return None
    return float(destination[0]), float(destination[1])


def bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def center_distance(a: Sequence[float], b: Sequence[float], diagonal: float) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    distance = math.hypot((ax + aw / 2) - (bx + bw / 2), (ay + ah / 2) - (by + bh / 2))
    return distance / max(diagonal, 1.0)


def point_to_rect_distance(point: Point, rect: Dict[str, float]) -> float:
    px, py = point
    left, top = rect["x"], rect["y"]
    right, bottom = left + rect["w"], top + rect["h"]
    dx = max(left - px, 0.0, px - right)
    dy = max(top - py, 0.0, py - bottom)
    return math.hypot(dx, dy)


def point_in_rect(point: Optional[Point], rect: Dict[str, float]) -> bool:
    if point is None:
        return False
    return (
        rect["x"] <= point[0] <= rect["x"] + rect["w"]
        and rect["y"] <= point[1] <= rect["y"] + rect["h"]
    )


def containing_zones(point: Optional[Point], zones: Iterable[Dict[str, float]]) -> List[Dict[str, float]]:
    return [zone for zone in zones if point_in_rect(point, zone)]


def nearest_table(
    point: Optional[Point],
    tables: Iterable[Dict[str, float]],
    radius: float,
    zones: Optional[Iterable[Dict[str, float]]] = None,
) -> Optional[Dict[str, float]]:
    if point is None:
        return None
    tables = list(tables)
    if zones:
        table_by_id = {table["id"]: table for table in tables}
        for zone in containing_zones(point, zones):
            if zone.get("type") == "seat" and zone.get("tableId") in table_by_id:
                return table_by_id[zone["tableId"]]
    candidates = [(point_to_rect_distance(point, table), table) for table in tables]
    if not candidates:
        return None
    distance, table = min(candidates, key=lambda item: item[0])
    return table if distance <= radius else None


def cosine_similarity(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> float:
    if a is None or b is None:
        return -1.0
    left = np.asarray(a, dtype=np.float32).reshape(-1)
    right = np.asarray(b, dtype=np.float32).reshape(-1)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator else -1.0


def average_embeddings(embeddings: Sequence[Sequence[float]]) -> Optional[List[float]]:
    if not embeddings:
        return None
    average = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0)
    norm = float(np.linalg.norm(average))
    if norm:
        average /= norm
    return average.astype(float).tolist()
