from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import cv2

from .geometry import average_embeddings, bbox_iou, containing_zones, cosine_similarity, homography_from_calibration, nearest_table, project_point
from .consistency import autocorrect_camera_maps, deduplicate_same_frame, enforce_exclusive_identity
from .presence import SeatedPresence
from .spatial_sync import refine_spatial_time
from .tracking import ByteTracker, Track, optimal_assignment
from .vision import VisionModels


ProgressCallback = Callable[[float, str], None]


class IdentityBank:
    def __init__(self, staff: Iterable[Dict], customer_threshold: float = 0.43, staff_threshold: float = 0.42) -> None:
        self.staff = [entry for entry in staff if entry.get("embedding")]
        self.customer_threshold = customer_threshold
        self.staff_threshold = staff_threshold
        self.customers: Dict[str, List[List[float]]] = {}
        self.next_customer = 1
        self.lock = threading.Lock()

    def resolve(self, embedding: Sequence[float], current_id: Optional[str] = None) -> Tuple[str, str, float]:
        with self.lock:
            staff_scores = [
                (cosine_similarity(embedding, entry["embedding"]), entry)
                for entry in self.staff
            ]
            if staff_scores:
                score, entry = max(staff_scores, key=lambda item: item[0])
                if score >= self.staff_threshold:
                    return f"staff:{entry['id']}", "staff", score

            if current_id and current_id.startswith("customer:"):
                samples = self.customers.setdefault(current_id, [])
                score = cosine_similarity(embedding, average_embeddings(samples)) if samples else 1.0
                # A local tracker can switch people during an occlusion. Do not
                # contaminate the customer's reference features with a mismatch.
                if score >= self.customer_threshold:
                    samples.append(list(embedding))
                    self.customers[current_id] = samples[-20:]
                return current_id, "customer", score

            candidates = [
                (cosine_similarity(embedding, average_embeddings(samples)), identity)
                for identity, samples in self.customers.items()
            ]
            if candidates:
                score, identity = max(candidates, key=lambda item: item[0])
                if score >= self.customer_threshold:
                    self.customers[identity].append(list(embedding))
                    self.customers[identity] = self.customers[identity][-20:]
                    return identity, "customer", score

            identity = f"customer:{self.next_customer:03d}"
            self.next_customer += 1
            self.customers[identity] = [list(embedding)]
            return identity, "customer", 1.0

    def anonymous(self) -> str:
        with self.lock:
            identity = f"customer:{self.next_customer:03d}"
            self.next_customer += 1
            self.customers.setdefault(identity, [])
            return identity


class VideoAnalyzer:
    def __init__(self, root: Path, project: Dict) -> None:
        self.root = Path(root)
        self.project = project
        self.models = VisionModels(self.root / "models")
        settings = project.get("settings", {})
        self.sample_fps = max(0.5, min(float(settings.get("sampleFps", 3)), 10.0))
        self.identity_bank = IdentityBank(
            project.get("staff", []),
            float(settings.get("customerFaceThreshold", 0.43)),
            float(settings.get("staffFaceThreshold", 0.42)),
        )

    @staticmethod
    def _apply_manual_marks(
        observations: List[Dict], marks: List[Dict], camera: Dict, timestamp: float,
        width: int, height: int, homography, tables: List[Dict], zones: List[Dict], table_radius: float,
    ) -> None:
        """Use a bounded human assertion where detection is missing or mislabelled."""
        local_time = timestamp - float(camera.get("timeOffsetSeconds", 0))
        active = [mark for mark in marks if float(mark["startSeconds"]) - 1e-3 <= local_time <= float(mark["endSeconds"]) + 1e-3]
        claimed = set()
        table_by_id = {table["id"]: table for table in tables}
        for mark in active:
            mx, my, mw, mh = mark["bbox"]
            selected = None
            best = 0.0
            for index, observation in enumerate(observations):
                if index in claimed:
                    continue
                ox, oy, ow, oh = observation["bbox"]
                overlap = bbox_iou([mx, my, mw, mh], [ox, oy, ow, oh])
                center_inside = mx <= ox + ow / 2 <= mx + mw and my <= oy + oh / 2 <= my + mh
                score = overlap + (0.12 if center_inside else 0.0)
                if score > best and (overlap >= 0.08 or center_inside and overlap >= 0.025):
                    selected, best = index, score

            identity = f"staff:{mark['staffId']}" if mark["role"] == "staff" else f"customer:manual:{mark['id']}"
            table_id = mark.get("tableId")
            if selected is not None:
                claimed.add(selected)
                observation = observations[selected]
                observation.update(globalId=identity, role=mark["role"], identityConfidence=1.0,
                                   manualMarkId=mark["id"], source="manual-assisted")
                if table_id:
                    observation["tableId"] = table_id
                continue

            table = table_by_id.get(table_id)
            if table:
                point = (float(table["x"]) + float(table["w"]) / 2,
                         float(table["y"]) + float(table["h"]) / 2)
            else:
                point = project_point(((mx + mw / 2) * width, (my + mh) * height), homography)
                if not table_id:
                    nearest = nearest_table(point, tables, table_radius, zones)
                    table_id = nearest["id"] if nearest else None
            observations.append({
                "cameraId": camera["id"], "localId": f"manual:{mark['id']}",
                "time": round(timestamp, 3), "globalId": identity, "role": mark["role"],
                "bbox": [round(value, 5) for value in mark["bbox"]],
                "map": [round(value, 5) for value in point] if point else None,
                "tableId": table_id,
                "zoneIds": [zone["id"] for zone in containing_zones(point, zones)],
                "confidence": 0.0, "identityConfidence": 1.0,
                "manualMarkId": mark["id"], "source": "manual",
            })

    def analyze(self, progress: Optional[ProgressCallback] = None) -> Dict:
        progress = progress or (lambda value, message: None)
        cameras = [camera for camera in self.project.get("cameras", []) if camera.get("videoPath")]
        if not cameras:
            raise ValueError("Upload at least one camera video before analysis")

        observations: List[Dict] = []
        tracklets: List[Dict] = []
        total = len(cameras)
        for index, camera in enumerate(cameras):
            base = index / total
            span = 1.0 / total

            def camera_progress(value: float, message: str) -> None:
                progress(base + value * span, message)

            camera_observations, camera_tracklets = self._analyze_camera(camera, camera_progress)
            observations.extend(camera_observations)
            tracklets.extend(camera_tracklets)

        progress(0.90, "Checking shared movement and camera timing")
        observations, duplicate_issues = deduplicate_same_frame(observations)
        spatial_sync = refine_spatial_time(observations, tracklets, self.project)
        auto_calibration = autocorrect_camera_maps(observations, tracklets, self.project)
        impossible_before = enforce_exclusive_identity(observations)
        progress(0.92, "Joining people as they move between camera views")
        observations, tracklets, handoffs = self._link_cross_camera(observations, tracklets)
        impossible_after = enforce_exclusive_identity(observations)
        for observation in observations:
            observation.pop("localId", None)
        progress(0.96, "Calculating table and service metrics")
        results = self._build_results(observations, tracklets, handoffs)
        results["autoCalibration"] = auto_calibration
        results["spatialSync"] = spatial_sync
        results["identityConsistency"] = {
            "duplicateBoxesRemoved": len(duplicate_issues),
            "impossiblePositionsFlagged": impossible_before["count"] + impossible_after["count"],
            "examples": (duplicate_issues + impossible_before["examples"] + impossible_after["examples"])[:100],
        }
        results["runtime"] = self.models.runtime_info()
        progress(1.0, "Analysis complete")
        return results

    def _analyze_camera(self, camera: Dict, progress: ProgressCallback) -> Tuple[List[Dict], List[Dict]]:
        video_path = self.root / camera["videoPath"]
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise ValueError(f"Could not open video for {camera.get('name', camera['id'])}")

        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        step = max(1, round(fps / self.sample_fps))
        tracker = ByteTracker(max_missing=max(3, round(self.sample_fps * 2.5)))
        seated_presence = SeatedPresence(camera["id"])
        homography = homography_from_calibration(camera.get("calibration", []), width, height)
        observations: List[Dict] = []
        completed_tracklets: List[Track] = []
        offset = float(camera.get("timeOffsetSeconds", 0.0))
        tables = self.project.get("tables", [])
        zones = self.project.get("zones", [])
        table_radius = float(self.project.get("settings", {}).get("tableRadius", 0.075))
        marks = [mark for mark in self.project.get("manualMarks", []) if mark.get("cameraId") == camera["id"]]

        frame_index = 0
        sampled = 0
        while True:
            ok = capture.grab()
            if not ok:
                break
            if frame_index % step != 0:
                frame_index += 1
                continue
            ok, frame = capture.retrieve()
            if not ok:
                break
            timestamp = offset + frame_index / fps
            detections = self.models.detections(frame, frame_index=sampled)
            active, ended = tracker.update(detections, timestamp, frame.shape)
            completed_tracklets.extend(ended)
            frame_start = len(observations)

            for track in active:
                embedding = track.embedding
                if embedding is not None:
                    identity, role, identity_score = self.identity_bank.resolve(embedding, track.global_id)
                    if role == "staff" or track.global_id is None:
                        track.global_id, track.role = identity, role
                elif track.global_id is None and timestamp - track.first_seen >= 0.75:
                    track.global_id = self.identity_bank.anonymous()
                    track.role = "customer"
                    identity_score = 0.0
                else:
                    identity_score = 0.0

                x, y, box_width, box_height = track.bbox
                map_point = project_point((x + box_width / 2, y + box_height), homography)
                if map_point is not None:
                    track.positions.append((timestamp, map_point[0], map_point[1]))
                point_zones = containing_zones(map_point, zones)
                table = nearest_table(map_point, tables, table_radius, zones)
                observations.append(
                    {
                        "cameraId": camera["id"],
                        "localId": track.local_id,
                        "time": round(timestamp, 3),
                        "globalId": track.global_id or f"pending:{camera['id']}:{track.local_id}",
                        "role": track.role,
                        "bbox": [
                            round(x / width, 5),
                            round(y / height, 5),
                            round(box_width / width, 5),
                            round(box_height / height, 5),
                        ],
                        "map": [round(map_point[0], 5), round(map_point[1], 5)] if map_point else None,
                        "tableId": table["id"] if table else None,
                        "zoneIds": [zone["id"] for zone in point_zones],
                        "confidence": round(float(track.score), 4),
                        "identityConfidence": round(float(identity_score), 4),
                    }
                )

            if marks:
                frame_observations = observations[-len(active):] if active else []
                self._apply_manual_marks(frame_observations, marks, camera, timestamp, width, height,
                                         homography, tables, zones, table_radius)
                if active:
                    observations[-len(active):] = frame_observations
                else:
                    observations.extend(frame_observations)

            observations.extend(seated_presence.update(frame, observations[frame_start:], timestamp))

            sampled += 1
            if sampled % 10 == 0:
                fraction = min(frame_index / max(frame_count, 1), 0.99)
                progress(fraction, f"Analyzing {camera.get('name', camera['id'])}: {fraction:.0%}")
            frame_index += 1

        completed_tracklets.extend(tracker.finish())
        capture.release()
        tracklet_rows = [self._tracklet_row(camera["id"], track) for track in completed_tracklets]
        final_tracks = {track.local_id: track for track in completed_tracklets}
        for observation in observations:
            track = final_tracks.get(observation["localId"])
            if track and track.global_id and not observation.get("manualMarkId"):
                observation["globalId"] = track.global_id
                observation["role"] = track.role
        progress(1.0, f"Finished {camera.get('name', camera['id'])}")
        return observations, tracklet_rows

    def _tracklet_row(self, camera_id: str, track: Track) -> Dict:
        path_samples = []
        last_sample_time = float("-inf")
        for timestamp, x, y in track.positions:
            if timestamp - last_sample_time >= 0.75 or not path_samples:
                path_samples.append([round(timestamp, 3), round(x, 5), round(y, 5)])
                last_sample_time = timestamp
        if track.positions and path_samples and path_samples[-1][0] != round(track.positions[-1][0], 3):
            timestamp, x, y = track.positions[-1]
            path_samples.append([round(timestamp, 3), round(x, 5), round(y, 5)])
        return {
            "cameraId": camera_id,
            "localId": track.local_id,
            "globalId": track.global_id,
            "role": track.role,
            "firstSeen": round(track.first_seen, 3),
            "lastSeen": round(track.last_seen, 3),
            "embedding": track.embedding,
            "appearance": track.appearance,
            "pathSamples": path_samples,
            "startMap": list(track.positions[0][1:]) if track.positions else None,
            "endMap": list(track.positions[-1][1:]) if track.positions else None,
            "startZoneIds": [zone["id"] for zone in containing_zones(track.positions[0][1:] if track.positions else None, self.project.get("zones", []))],
            "endZoneIds": [zone["id"] for zone in containing_zones(track.positions[-1][1:] if track.positions else None, self.project.get("zones", []))],
        }

    def _camera_route(self, from_camera_id: str, to_camera_id: str) -> Optional[Dict]:
        """Return a normalized configured camera edge, or a legacy fallback."""

        settings = self.project.get("settings", {})
        links = self.project.get("cameraLinks", [])
        if not links:
            return {
                "id": None,
                "name": "Automatic fallback",
                "fromCameraId": from_camera_id,
                "toCameraId": to_camera_id,
                "mode": "handoff",
                "minSeconds": -1.5,
                "maxSeconds": float(settings.get("handoffSeconds", 8.0)),
                "maxDistance": float(settings.get("handoffDistance", 0.12)),
                "exitZoneId": None,
                "entryZoneId": None,
                "explicit": False,
            }
        for link in links:
            direct = link.get("fromCameraId") == from_camera_id and link.get("toCameraId") == to_camera_id
            reverse = (
                bool(link.get("bidirectional"))
                and link.get("fromCameraId") == to_camera_id
                and link.get("toCameraId") == from_camera_id
            )
            if not direct and not reverse:
                continue
            route = dict(link)
            route["explicit"] = True
            route["mode"] = route.get("mode") if route.get("mode") in {"handoff", "overlap"} else "handoff"
            route["minSeconds"] = float(route.get("minSeconds", -1.0 if route["mode"] == "overlap" else 0.0))
            route["maxSeconds"] = float(route.get("maxSeconds", settings.get("handoffSeconds", 8.0)))
            route["maxDistance"] = float(route.get("maxDistance", settings.get("handoffDistance", 0.12)))
            if reverse:
                route["fromCameraId"], route["toCameraId"] = from_camera_id, to_camera_id
                route["exitZoneId"], route["entryZoneId"] = route.get("entryZoneId"), route.get("exitZoneId")
            return route
        return None

    @staticmethod
    def _overlap_distance(source: Dict, target: Dict, tolerance: float = 0.6) -> Optional[float]:
        distances = []
        for source_point in source.get("pathSamples", []):
            for target_point in target.get("pathSamples", []):
                if abs(float(source_point[0]) - float(target_point[0])) <= tolerance:
                    distances.append(math.hypot(source_point[1] - target_point[1], source_point[2] - target_point[2]))
        return median(distances) if distances else None

    def _link_cross_camera(self, observations: List[Dict], tracklets: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        settings = self.project.get("settings", {})
        body_threshold = float(settings.get("bodyReidThreshold", 0.58))
        tracks = [
            track for track in tracklets
            if track.get("globalId") and track.get("startMap") and track.get("endMap")
        ]
        if not tracks:
            return observations, tracklets, []

        handoff_zone_ids = {
            zone["id"] for zone in self.project.get("zones", []) if zone.get("type") == "handoff"
        }
        candidates: Dict[Tuple[int, int], Dict] = {}
        for source_index, source in enumerate(tracks):
            for target_index, target in enumerate(tracks):
                if source_index == target_index or source["cameraId"] == target["cameraId"]:
                    continue
                route = self._camera_route(source["cameraId"], target["cameraId"])
                if route is None:
                    continue
                mode = route["mode"]
                if mode == "overlap":
                    source_order = (float(source["firstSeen"]), source["cameraId"], source["localId"])
                    target_order = (float(target["firstSeen"]), target["cameraId"], target["localId"])
                    if source_order >= target_order:
                        continue
                    overlap_start = max(float(source["firstSeen"]), float(target["firstSeen"]))
                    overlap_end = min(float(source["lastSeen"]), float(target["lastSeen"]))
                    if overlap_end + 0.6 < overlap_start:
                        continue
                    gap = float(target["firstSeen"] - source["lastSeen"])
                    distance = self._overlap_distance(source, target)
                    if distance is None:
                        continue
                else:
                    gap = float(target["firstSeen"] - source["lastSeen"])
                    if gap < route["minSeconds"] or gap > route["maxSeconds"]:
                        continue
                    distance = math.hypot(
                        target["startMap"][0] - source["endMap"][0],
                        target["startMap"][1] - source["endMap"][1],
                    )

                exit_zone = route.get("exitZoneId")
                entry_zone = route.get("entryZoneId")
                exit_ok = not exit_zone or exit_zone in source.get("endZoneIds", [])
                entry_ok = not entry_zone or entry_zone in target.get("startZoneIds", [])
                if not exit_ok or not entry_ok:
                    continue
                shared_handoff_zone = bool(
                    set(source.get("endZoneIds", []))
                    & set(target.get("startZoneIds", []))
                    & handoff_zone_ids
                )
                route_zone_support = bool(exit_zone or entry_zone) and exit_ok and entry_ok
                face_similarity = cosine_similarity(source.get("embedding"), target.get("embedding"))
                body_similarity = cosine_similarity(source.get("appearance"), target.get("appearance"))
                face_support = face_similarity >= self.identity_bank.customer_threshold - 0.04
                body_support = body_similarity >= body_threshold
                layout_support = distance <= route["maxDistance"]

                source_staff = str(source["globalId"]).startswith("staff:")
                target_staff = str(target["globalId"]).startswith("staff:")
                if source_staff and target_staff and source["globalId"] != target["globalId"]:
                    continue
                if source_staff != target_staff:
                    # A cross-camera appearance match must not relabel a guest
                    # as an enrolled employee (or the reverse). Such cases
                    # need an explicit human identity correction.
                    continue
                if mode == "overlap" and distance > route["maxDistance"] * 1.5:
                    # Similar faces cannot put one person in two distant map
                    # positions at the same instant.
                    continue
                if not (face_support or body_support or layout_support or shared_handoff_zone or route_zone_support):
                    continue
                if distance > route["maxDistance"] * 1.75 and not face_support:
                    continue

                distance_cost = min(distance / max(route["maxDistance"], 1e-3), 2.0) * 0.32
                if mode == "overlap":
                    time_cost = 0.0
                else:
                    time_span = max(route["maxSeconds"] - route["minSeconds"], 1.0)
                    time_cost = min(max(gap - route["minSeconds"], 0.0) / time_span, 1.0) * 0.18
                face_cost = (1.0 - max(face_similarity, 0.0)) * 0.18 if face_similarity >= 0 else 0.12
                body_cost = (1.0 - max(body_similarity, 0.0)) * 0.32 if body_similarity >= 0 else 0.20
                zone_bonus = 0.10 if route_zone_support else 0.06 if shared_handoff_zone else 0.0
                cost = max(0.0, distance_cost + time_cost + face_cost + body_cost - zone_bonus)
                if cost > 0.88:
                    continue
                candidates[(source_index, target_index)] = {
                    "cost": cost,
                    "distance": distance,
                    "gap": gap,
                    "faceSimilarity": face_similarity,
                    "bodySimilarity": body_similarity,
                    "faceSupport": face_support,
                    "bodySupport": body_support,
                    "sharedHandoffZone": shared_handoff_zone,
                    "routeZoneSupport": route_zone_support,
                    "route": route,
                }

        cost_matrix = [[1_000.0 for _ in tracks] for _ in tracks]
        for (source_index, target_index), candidate in candidates.items():
            cost_matrix[source_index][target_index] = candidate["cost"]
        assignments = optimal_assignment(cost_matrix, 0.88) if candidates else []

        parent: Dict[str, str] = {}

        def find(identity: str) -> str:
            parent.setdefault(identity, identity)
            if parent[identity] != identity:
                parent[identity] = find(parent[identity])
            return parent[identity]

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root == right_root:
                return
            if left_root.startswith("staff:") != right_root.startswith("staff:"):
                canonical = left_root if left_root.startswith("staff:") else right_root
            else:
                canonical = min(left_root, right_root)
            other = right_root if canonical == left_root else left_root
            parent[other] = canonical

        handoffs = []
        for source_index, target_index in sorted(assignments, key=lambda pair: tracks[pair[1]]["firstSeen"]):
            candidate = candidates.get((source_index, target_index))
            if candidate is None:
                continue
            source, target = tracks[source_index], tracks[target_index]
            union(source["globalId"], target["globalId"])
            route = candidate["route"]
            evidence = []
            if candidate["faceSupport"]:
                evidence.append("face")
            if candidate["bodySupport"]:
                evidence.append("body ReID")
            if route["mode"] == "overlap":
                evidence.append("overlap geometry")
            elif candidate["routeZoneSupport"]:
                evidence.append("configured route zones")
            elif candidate["sharedHandoffZone"]:
                evidence.append("shared handoff zone")
            else:
                evidence.append("layout continuity")
            handoffs.append(
                {
                    "fromCameraId": source["cameraId"],
                    "toCameraId": target["cameraId"],
                    "time": round(max(float(source["lastSeen"]), float(target["firstSeen"])), 2),
                    "distance": round(candidate["distance"], 4),
                    "timeGapSeconds": round(candidate["gap"], 2),
                    "faceSimilarity": round(candidate["faceSimilarity"], 4) if candidate["faceSimilarity"] >= 0 else None,
                    "bodySimilarity": round(candidate["bodySimilarity"], 4) if candidate["bodySimilarity"] >= 0 else None,
                    "sharedHandoffZone": candidate["sharedHandoffZone"],
                    "routeId": route.get("id"),
                    "routeName": route.get("name") or "Camera route",
                    "mode": route["mode"],
                    "matchConfidence": round(max(0.0, 1.0 - candidate["cost"]), 4),
                    "reviewRequired": not (candidate["faceSupport"] or candidate["bodySupport"] or route["mode"] == "overlap"),
                    "basis": " + ".join(evidence),
                    "sourceId": source["globalId"],
                    "targetId": target["globalId"],
                }
            )

        remap = {identity: find(identity) for identity in list(parent)}
        for observation in observations:
            observation["globalId"] = remap.get(observation["globalId"], observation["globalId"])
            if observation["globalId"].startswith("staff:"):
                observation["role"] = "staff"
        for track in tracklets:
            track["globalId"] = remap.get(track.get("globalId"), track.get("globalId"))
            if str(track.get("globalId", "")).startswith("staff:"):
                track["role"] = "staff"
        for handoff in handoffs:
            handoff["globalId"] = remap.get(handoff["sourceId"], handoff["sourceId"])
        return observations, tracklets, handoffs

    def _build_results(self, observations: List[Dict], tracklets: List[Dict], handoffs: Optional[List[Dict]] = None) -> Dict:
        observations = [observation for observation in observations if not observation["globalId"].startswith("pending:")]
        interval = 1.0 / self.sample_fps
        buckets: Dict[str, Dict[int, Dict[str, Set[str]]]] = defaultdict(
            lambda: defaultdict(lambda: {"customers": set(), "staff": set()})
        )
        trajectories: Dict[str, List[Dict]] = defaultdict(list)
        seen_trajectory_bucket: Set[Tuple[str, int, str]] = set()
        for observation in observations:
            identity = observation["globalId"]
            if observation.get("map"):
                second = int(observation["time"])
                key = (identity, second, observation["cameraId"])
                if key not in seen_trajectory_bucket:
                    trajectories[identity].append(
                        {
                            "time": observation["time"],
                            "cameraId": observation["cameraId"],
                            "map": observation["map"],
                            "role": observation["role"],
                        }
                    )
                    seen_trajectory_bucket.add(key)
            table_id = observation.get("tableId")
            if not table_id:
                continue
            bucket = int(round(observation["time"] / interval))
            if observation["role"] not in {"staff", "customer"}:
                continue
            group = "staff" if observation["role"] == "staff" else "customers"
            buckets[table_id][bucket][group].add(identity)

        table_rows = []
        sessions_all = []
        staff_stats: Dict[str, Dict] = defaultdict(
            lambda: {"tableVisits": 0, "interactionSeconds": 0.0, "tables": set(), "firstContacts": 0}
        )
        for table in self.project.get("tables", []):
            timeline = buckets.get(table["id"], {})
            occupied_buckets = sorted(bucket for bucket, groups in timeline.items() if groups["customers"])
            sessions = self._contiguous_groups(occupied_buckets, max_gap=max(1, round(2.0 / interval)))
            table_sessions = []
            total_visits = 0
            first_service_times = []
            party_sizes = []
            for session_index, session in enumerate(sessions, 1):
                start_bucket, end_bucket = session[0], session[-1]
                customer_ids = set().union(*(timeline[bucket]["customers"] for bucket in session))
                staff_by_bucket = {bucket: timeline[bucket]["staff"] for bucket in session if timeline[bucket]["staff"]}
                staff_buckets = sorted(staff_by_bucket)
                visits = self._contiguous_groups(staff_buckets, max_gap=max(1, round(2.0 / interval)))
                total_visits += len(visits)
                party_sizes.append(len(customer_ids))
                first_service = (staff_buckets[0] - start_bucket) * interval if staff_buckets else None
                if first_service is not None:
                    first_service_times.append(first_service)
                    for identity in staff_by_bucket[staff_buckets[0]]:
                        staff_stats[identity]["firstContacts"] += 1
                for visit in visits:
                    identities = set().union(*(staff_by_bucket.get(bucket, set()) for bucket in visit))
                    duration = len(visit) * interval
                    for identity in identities:
                        staff_stats[identity]["tableVisits"] += 1
                        staff_stats[identity]["interactionSeconds"] += duration
                        staff_stats[identity]["tables"].add(table["number"])
                row = {
                    "id": f"{table['id']}-session-{session_index}",
                    "tableId": table["id"],
                    "tableNumber": table["number"],
                    "start": round(start_bucket * interval, 2),
                    "end": round(end_bucket * interval, 2),
                    "durationSeconds": round((end_bucket - start_bucket + 1) * interval, 2),
                    "partySize": len(customer_ids),
                    "customerIds": sorted(customer_ids),
                    "firstServiceSeconds": round(first_service, 2) if first_service is not None else None,
                    "staffVisits": len(visits),
                }
                table_sessions.append(row)
                sessions_all.append(row)

            table_rows.append(
                {
                    "tableId": table["id"],
                    "tableNumber": table["number"],
                    "turns": len(table_sessions),
                    "occupiedMinutes": round(sum(item["durationSeconds"] for item in table_sessions) / 60.0, 2),
                    "averagePartySize": round(sum(party_sizes) / len(party_sizes), 1) if party_sizes else 0,
                    "averageFirstServiceSeconds": round(sum(first_service_times) / len(first_service_times), 1) if first_service_times else None,
                    "staffVisits": total_visits,
                }
            )

        staff_names = {f"staff:{entry['id']}": entry["name"] for entry in self.project.get("staff", [])}
        staff_rows = []
        for identity, stats in sorted(staff_stats.items()):
            staff_rows.append(
                {
                    "id": identity,
                    "name": staff_names.get(identity, identity.replace("staff:", "Staff ")),
                    "tableVisits": stats["tableVisits"],
                    "interactionMinutes": round(stats["interactionSeconds"] / 60.0, 2),
                    "tablesServed": sorted(stats["tables"]),
                    "firstContacts": stats["firstContacts"],
                }
            )

        customer_ids = {observation["globalId"] for observation in observations if observation["role"] == "customer"}
        first_service_values = [session["firstServiceSeconds"] for session in sessions_all if session["firstServiceSeconds"] is not None]
        served_sessions = [session for session in sessions_all if session["staffVisits"] > 0]
        mapped_observations = sum(1 for observation in observations if observation.get("map"))
        average_detection_confidence = (
            sum(float(observation.get("confidence", 0)) for observation in observations if not observation.get("estimated") and observation.get("source") != "manual")
            / max(1, sum(1 for observation in observations if not observation.get("estimated") and observation.get("source") != "manual"))
        )
        return {
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": {
                "customers": len(customer_ids),
                "staffDetected": len(staff_rows),
                "tableSessions": len(sessions_all),
                "observations": len(observations),
                "manualObservations": sum(1 for observation in observations if observation.get("manualMarkId")),
                "estimatedSeatedObservations": sum(1 for observation in observations if observation.get("estimated")),
                "observationsNeedingIdentityReview": sum(1 for observation in observations if observation.get("role") == "unresolved"),
                "camerasAnalyzed": len({observation["cameraId"] for observation in observations}),
                "occupiedMinutes": round(sum(session["durationSeconds"] for session in sessions_all) / 60.0, 2),
                "staffVisits": sum(session["staffVisits"] for session in sessions_all),
                "averageFirstServiceSeconds": round(sum(first_service_values) / len(first_service_values), 1) if first_service_values else None,
                "serviceCoveragePercent": round(len(served_sessions) / len(sessions_all) * 100, 1) if sessions_all else 0,
                "mappedObservationPercent": round(mapped_observations / len(observations) * 100, 1) if observations else 0,
                "averageDetectionConfidence": round(average_detection_confidence, 3),
                "crossCameraHandoffs": len(handoffs or []),
                "handoffsNeedingReview": sum(1 for handoff in (handoffs or []) if handoff.get("reviewRequired")),
            },
            "trackingProfile": {
                "singleCamera": "ByteTrack-style two-stage association",
                "bodyReidentification": "OpenVINO person-reidentification-retail-0288"
                if getattr(getattr(self, "models", None), "body_reid_net", None) is not None
                else "HSV appearance fallback",
                "crossCamera": "globally optimized route-aware tracklet assignment",
                "configuredCameraRoutes": len(self.project.get("cameraLinks", [])),
            },
            "tables": table_rows,
            "staff": staff_rows,
            "sessions": sessions_all,
            "trajectories": trajectories,
            "tracklets": [
                {key: value for key, value in tracklet.items() if key not in {"embedding", "appearance"}}
                for tracklet in tracklets
            ],
            "handoffs": handoffs or [],
            "observations": observations,
            "methodNotes": [
                "Customer identities are temporary clusters strengthened by whole-body ReID and occasional face evidence; biometric vectors are not retained in results.",
                "Camera points are projected onto the shared floor plan using manual calibration.",
                "Within-camera tracks use high-confidence association followed by low-confidence occlusion recovery.",
                "Seated customer positions may persist through missed detections while the camera view remains visually consistent; these observations are estimated and end after visual change or observed departure.",
                "On-screen timestamps establish the camera clock baseline. Shared movement can refine an offset only when two independent moving staff identities provide a clear match.",
                "Cross-camera handoffs use a global assignment across configured camera routes, travel windows, zones, body appearance, face evidence and floor position.",
                "First service means the first detected staff presence near an occupied table.",
                "All metrics should be reviewed against the linked footage during calibration.",
                "Manual markings are time-bounded human assertions. They can fill detection gaps but must end when the person leaves; they are not proof of continuous visual tracking.",
                "Distinct people in one camera frame cannot share an identity. Conflicting customer boxes receive separate provisional IDs; conflicting staff matches are withheld from staff metrics pending review. Strong multi-person overlap evidence may translate a camera's analysis map but never silently rewrites calibration points.",
            ],
        }

    @staticmethod
    def _contiguous_groups(values: Sequence[int], max_gap: int) -> List[List[int]]:
        if not values:
            return []
        groups = [[values[0]]]
        for value in values[1:]:
            if value - groups[-1][-1] <= max_gap:
                groups[-1].append(value)
            else:
                groups.append([value])
        return groups
