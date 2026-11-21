"""Conservative spatial corrections and impossible-identity guards."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Dict, List, Tuple

from .geometry import bbox_iou, containing_zones, nearest_table


def _quality(row: Dict) -> float:
    return (3.0 if row.get("manualMarkId") else 0.0) + float(row.get("identityConfidence") or 0) * 1.5 + float(row.get("confidence") or 0)


def deduplicate_same_frame(observations: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """A single physical box cannot be both a guest and an employee."""
    groups = defaultdict(list)
    for row in observations:
        groups[(row["cameraId"], row["time"])].append(row)
    retained, issues = [], []
    for (camera_id, timestamp), rows in groups.items():
        kept = []
        for row in sorted(rows, key=_quality, reverse=True):
            duplicate = next((other for other in kept if bbox_iou(row["bbox"], other["bbox"]) >= .68), None)
            if duplicate is not None:
                if len(issues) < 1000:
                    issues.append({"type": "overlapping-person-boxes", "cameraId": camera_id,
                                   "time": timestamp, "keptId": duplicate["globalId"],
                                   "removedId": row["globalId"]})
                continue
            kept.append(row)
        retained.extend(kept)
    return retained, issues


def _unresolve(row: Dict, reason: str) -> None:
    if row.get("needsReview") and str(row.get("globalId", "")).startswith(("customer:collision:", "unresolved:collision:")):
        return
    row["previousIdentity"] = row["globalId"]
    prefix = "customer" if row.get("role") == "customer" else "unresolved"
    anchor = row.get("manualMarkId") or row.get("localId")
    if anchor is None:
        anchor = ":".join(str(round(float(value), 2)) for value in row.get("bbox", []))
    row["globalId"] = f"{prefix}:collision:{row['cameraId']}:{anchor}"
    if prefix == "unresolved":
        row["role"] = "unresolved"
    row["needsReview"] = reason


def enforce_same_frame_exclusivity(observations: List[Dict]) -> Dict:
    """Keep every distinct person box, but never give two boxes one ID in a frame.

    Collision IDs follow a local track/manual mark where possible. For older
    saved results without local IDs, nearby displaced boxes reuse a collision
    ID across successive frames rather than creating a new person each frame.
    """
    frames = defaultdict(list)
    for row in observations:
        frames[(row["cameraId"], row["time"])].append(row)
    existing_ids = {row["globalId"] for row in observations}
    active = defaultdict(list)
    serial = 0
    issues = []
    count = 0
    for (camera_id, timestamp), rows in sorted(frames.items(), key=lambda item: (item[0][0], item[0][1])):
        by_identity = defaultdict(list)
        for row in rows:
            by_identity[row["globalId"]].append(row)
        used_ids = set(by_identity)
        for identity, duplicates in by_identity.items():
            if len(duplicates) < 2:
                continue
            ordered = sorted(duplicates, key=_quality, reverse=True)
            for row in ordered[1:]:
                anchor = row.get("manualMarkId") or row.get("localId")
                position = row.get("bbox") or [0, 0, 0, 0]
                center = (float(position[0]) + float(position[2]) / 2,
                          float(position[1]) + float(position[3]) / 2)
                candidates = active[(camera_id, identity)]
                match = None
                best = float("inf")
                for candidate in candidates:
                    if candidate["id"] in used_ids or candidate["time"] >= timestamp:
                        continue
                    if anchor is not None:
                        if candidate["anchor"] != str(anchor):
                            continue
                        distance = 0
                    else:
                        if candidate["anchor"] is not None or timestamp - candidate["time"] > 1.5:
                            continue
                        distance = math.dist(center, candidate["center"])
                        if distance > .16:
                            continue
                    if distance < best:
                        match, best = candidate, distance
                if match is None:
                    prefix = "customer" if row.get("role") == "customer" else "unresolved"
                    while True:
                        serial += 1
                        new_id = f"{prefix}:collision:{camera_id}:{serial}"
                        if new_id not in existing_ids:
                            break
                    existing_ids.add(new_id)
                    match = {"id": new_id, "anchor": str(anchor) if anchor is not None else None}
                    candidates.append(match)
                match["time"] = timestamp
                match["center"] = center
                row["previousIdentity"] = identity
                row["globalId"] = match["id"]
                if row.get("role") == "staff":
                    row["role"] = "unresolved"
                row["needsReview"] = "simultaneous-position-conflict"
                used_ids.add(match["id"])
                count += 1
                if len(issues) < 100:
                    issues.append({"type": "same-frame-identity-collision", "cameraId": camera_id,
                                   "time": timestamp, "identity": identity, "splitId": match["id"]})
    return {"count": count, "examples": issues}


def enforce_exclusive_identity(observations: List[Dict], max_time_gap: float = .45,
                               max_map_distance: float = .14) -> Dict:
    """Downgrade contradictory observations instead of claiming one person is in two places."""
    frame_issues = enforce_same_frame_exclusivity(observations)
    by_identity = defaultdict(list)
    for row in observations:
        if row.get("role") in {"customer", "staff"}:
            by_identity[row["globalId"]].append(row)
    issues = []
    issue_count = frame_issues["count"]
    issues.extend(frame_issues["examples"])
    for identity, rows in by_identity.items():
        rows.sort(key=lambda item: item["time"])
        for index, left in enumerate(rows):
            right_index = index + 1
            while right_index < len(rows) and rows[right_index]["time"] - left["time"] <= max_time_gap:
                right = rows[right_index]
                if left.get("globalId") != identity or right.get("globalId") != identity:
                    right_index += 1
                    continue
                same_camera = left["cameraId"] == right["cameraId"]
                if same_camera:
                    contradiction = left["time"] == right["time"] and bbox_iou(left["bbox"], right["bbox"]) < .2
                else:
                    a, b = left.get("map"), right.get("map")
                    contradiction = bool(a and b and math.dist(a, b) > max_map_distance)
                if contradiction:
                    if _quality(left) > _quality(right) + .1:
                        _unresolve(right, "simultaneous-position-conflict")
                    elif _quality(right) > _quality(left) + .1:
                        _unresolve(left, "simultaneous-position-conflict")
                    else:
                        _unresolve(left, "ambiguous-simultaneous-position")
                        _unresolve(right, "ambiguous-simultaneous-position")
                    issue_count += 1
                    if len(issues) < 100:
                        issues.append({"type": "impossible-simultaneous-identity", "identity": identity,
                                       "cameraIds": [left["cameraId"], right["cameraId"]],
                                       "time": round((left["time"] + right["time"]) / 2, 2)})
                right_index += 1
    final_frame_issues = enforce_same_frame_exclusivity(observations)
    issue_count += final_frame_issues["count"]
    issues.extend(final_frame_issues["examples"][:max(0, 100 - len(issues))])
    return {"count": issue_count, "examples": issues[:100]}


def autocorrect_camera_maps(observations: List[Dict], tracklets: List[Dict], project: Dict) -> Dict:
    """Translate a camera map only when independent staff tracks agree on the error."""
    cameras = project.get("cameras", [])
    if len(cameras) < 2:
        return {"status": "single-camera", "cameras": []}
    reference_id = cameras[0]["id"]
    by_camera_identity_second = defaultdict(list)
    for row in observations:
        if (row.get("role") == "staff" and row.get("map") and
                (float(row.get("identityConfidence") or 0) >= .5 or row.get("manualMarkId"))):
            by_camera_identity_second[(row["cameraId"], row["globalId"], round(row["time"]))].append(row)
    rows = [{"cameraId": reference_id, "status": "reference", "shift": [0, 0]}]
    zones = project.get("zones", [])
    tables = project.get("tables", [])
    table_radius = float(project.get("settings", {}).get("tableRadius", .075))
    for camera in cameras[1:]:
        camera_id = camera["id"]
        residuals = []
        for (source_camera, identity, second), source_rows in by_camera_identity_second.items():
            if source_camera != reference_id:
                continue
            target_rows = by_camera_identity_second.get((camera_id, identity, second), [])
            if not target_rows:
                continue
            source = max(source_rows, key=_quality)
            target = min(target_rows, key=lambda item: abs(item["time"] - source["time"]))
            if abs(source["time"] - target["time"]) > .45:
                continue
            residuals.append((source["map"][0] - target["map"][0],
                              source["map"][1] - target["map"][1], identity, second))
        identities = {item[2] for item in residuals}
        if len(residuals) < 8 or len(identities) < 2:
            rows.append({"cameraId": camera_id, "status": "insufficient-evidence",
                         "pairs": len(residuals), "identities": len(identities)})
            continue
        dx = median(item[0] for item in residuals)
        dy = median(item[1] for item in residuals)
        inliers = [item for item in residuals if math.hypot(item[0] - dx, item[1] - dy) <= .025]
        inlier_ids = {item[2] for item in inliers}
        support_by_id = {identity: {item[3] for item in inliers if item[2] == identity} for identity in inlier_ids}
        independent_ids = {identity for identity, seconds in support_by_id.items()
                           if len(seconds) >= 3 and max(seconds) - min(seconds) >= 5}
        if len(inliers) < 8 or len(independent_ids) < 2 or len(inliers) / len(residuals) < .7:
            rows.append({"cameraId": camera_id, "status": "conflicting-evidence",
                         "pairs": len(residuals), "inliers": len(inliers),
                         "identities": len(independent_ids)})
            continue
        dx = median(item[0] for item in inliers)
        dy = median(item[1] for item in inliers)
        magnitude = math.hypot(dx, dy)
        if magnitude < .015:
            rows.append({"cameraId": camera_id, "status": "aligned",
                         "pairs": len(inliers), "identities": len(inlier_ids), "shift": [0, 0]})
            continue
        if magnitude > .12:
            rows.append({"cameraId": camera_id, "status": "large-error-needs-review",
                         "pairs": len(inliers), "identities": len(inlier_ids),
                         "proposedShift": [round(dx, 4), round(dy, 4)]})
            continue
        for observation in observations:
            if observation["cameraId"] != camera_id or not observation.get("map"):
                continue
            point = [max(0, min(1, observation["map"][0] + dx)),
                     max(0, min(1, observation["map"][1] + dy))]
            observation["map"] = [round(value, 5) for value in point]
            observation["zoneIds"] = [zone["id"] for zone in containing_zones(point, zones)]
            if not observation.get("manualMarkId"):
                table = nearest_table(point, tables, table_radius, zones)
                observation["tableId"] = table["id"] if table else None
        for track in tracklets:
            if track["cameraId"] != camera_id:
                continue
            for key in ("startMap", "endMap"):
                if track.get(key):
                    track[key] = [round(max(0, min(1, track[key][0] + dx)), 5),
                                  round(max(0, min(1, track[key][1] + dy)), 5)]
            for point in track.get("pathSamples", []):
                point[1] = round(max(0, min(1, point[1] + dx)), 5)
                point[2] = round(max(0, min(1, point[2] + dy)), 5)
            track["startZoneIds"] = [zone["id"] for zone in containing_zones(track.get("startMap"), zones)]
            track["endZoneIds"] = [zone["id"] for zone in containing_zones(track.get("endMap"), zones)]
        rows.append({"cameraId": camera_id, "status": "corrected", "shift": [round(dx, 4), round(dy, 4)],
                     "pairs": len(inliers), "identities": len(inlier_ids)})
    return {"status": "checked", "referenceCameraId": reference_id, "cameras": rows,
            "correctedCameras": sum(row["status"] == "corrected" for row in rows)}
