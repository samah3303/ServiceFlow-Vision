"""Conservative time refinement from motion seen in overlapping camera views."""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from statistics import median
from typing import Dict, List, Optional, Tuple


def _series(observations: List[Dict]) -> Dict[Tuple[str, str], List[Tuple[float, float, float]]]:
    buckets = defaultdict(list)
    for row in observations:
        if (row.get("role") == "staff" and row.get("map") and not row.get("estimated")
                and float(row.get("identityConfidence") or 0) >= .6):
            buckets[(row["cameraId"], row["globalId"], round(float(row["time"])))].append(row["map"])
    grouped = defaultdict(list)
    for (camera_id, identity, second), points in buckets.items():
        grouped[(camera_id, identity)].append((float(second), median(p[0] for p in points),
                                               median(p[1] for p in points)))
    return {key: sorted(points) for key, points in grouped.items()}


def _moving_points(points: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
    return [point for index, point in enumerate(points) if index >= 2 and
            point[0] - points[index - 2][0] <= 4 and
            math.hypot(point[1] - points[index - 2][1], point[2] - points[index - 2][2]) >= .025]


def _score(source: Dict[str, List[Tuple[float, float, float]]],
           target: Dict[str, List[Tuple[float, float, float]]], lag: float) -> Optional[Dict]:
    residuals = []
    independent = set()
    for identity, moving in source.items():
        counterpart = target.get(identity, [])
        if not counterpart:
            continue
        times = [point[0] for point in counterpart]
        matched = []
        for point in moving:
            desired = point[0] + lag
            index = bisect.bisect_left(times, desired)
            choices = [counterpart[i] for i in (index - 1, index) if 0 <= i < len(counterpart)]
            if not choices:
                continue
            other = min(choices, key=lambda item: abs(item[0] - desired))
            if abs(other[0] - desired) <= .35:
                matched.append((point[1] - other[1], point[2] - other[2]))
        if len(matched) >= 12:
            independent.add(identity)
            residuals.extend((dx, dy, identity) for dx, dy in matched)
    if len(independent) < 2 or len(residuals) < 30:
        return None
    dx, dy = median(row[0] for row in residuals), median(row[1] for row in residuals)
    errors = [math.hypot(row[0] - dx, row[1] - dy) for row in residuals]
    return {"lag": lag, "error": median(errors), "pairs": len(errors), "identities": len(independent)}


def refine_spatial_time(observations: List[Dict], tracklets: List[Dict], project: Dict) -> Dict:
    """Shift analysis time only when two moving enrolled staff agree.

    Burned-in clocks are the baseline. We search for a small residual lag in
    shared movement, subtracting spatial translation before comparing paths.
    Weak/flat/conflicting evidence never changes the timeline.
    """
    cameras = project.get("cameras", [])
    if len(cameras) < 2:
        return {"status": "single-camera", "cameras": []}
    reference = cameras[0]["id"]
    series = _series(observations)
    rows = [{"cameraId": reference, "status": "reference", "adjustmentSeconds": 0.0}]
    for camera in cameras[1:]:
        target_id = camera["id"]
        identities = {identity for cam, identity in series if cam == reference and (target_id, identity) in series}
        source = {identity: _moving_points(series[(reference, identity)]) for identity in identities}
        target = {identity: series[(target_id, identity)] for identity in identities}
        if sum(len(points) >= 15 for points in source.values()) < 2:
            rows.append({"cameraId": target_id, "status": "insufficient-movement", "adjustmentSeconds": 0.0})
            continue
        scores = [value for lag_step in range(-24, 25)
                  if (value := _score(source, target, lag_step * .5)) is not None]
        if not scores:
            rows.append({"cameraId": target_id, "status": "insufficient-overlap", "adjustmentSeconds": 0.0})
            continue
        best = min(scores, key=lambda item: item["error"])
        baseline = next((item for item in scores if item["lag"] == 0), None)
        rivals = [item for item in scores if abs(item["lag"] - best["lag"]) > 1.5]
        rival = min(rivals, key=lambda item: item["error"]) if rivals else None
        evidence = {"cameraId": target_id, "adjustmentSeconds": 0.0,
                    "candidateLagSeconds": best["lag"], "medianMapError": round(best["error"], 4),
                    "pairs": best["pairs"], "identities": best["identities"]}
        if abs(best["lag"]) < .75 and best["error"] < .055:
            evidence["status"] = "aligned"
        elif (not baseline or not rival or abs(best["lag"]) >= 11.5 or best["error"] >= .055
              or baseline["error"] - best["error"] < .015 or rival["error"] - best["error"] < .008):
            evidence["status"] = "ambiguous-needs-review"
        else:
            shift = -best["lag"]
            evidence.update(status="corrected", adjustmentSeconds=shift,
                            improvement=round(baseline["error"] - best["error"], 4))
            for observation in observations:
                if observation["cameraId"] == target_id:
                    observation["time"] = round(float(observation["time"]) + shift, 3)
            for track in tracklets:
                if track["cameraId"] != target_id:
                    continue
                for key in ("firstSeen", "lastSeen"):
                    if key in track:
                        track[key] = round(float(track[key]) + shift, 3)
                for point in track.get("pathSamples", []):
                    point[0] = round(float(point[0]) + shift, 3)
        rows.append(evidence)
    return {"status": "checked", "referenceCameraId": reference, "cameras": rows,
            "correctedCameras": sum(row["status"] == "corrected" for row in rows)}
