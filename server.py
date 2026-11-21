#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import cgi
import copy
import json
import math
import mimetypes
import os
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from app.analytics import VideoAnalyzer
from app.consistency import enforce_same_frame_exclusivity
from app.timestamps import synchronize_project_cameras
from app.vision import VisionModels


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
PROJECT_PATH = DATA_DIR / "project.json"
RESULTS_PATH = DATA_DIR / "results.json"
RUNS_DIR = DATA_DIR / "runs"
TASKS_PATH = DATA_DIR / "tasks.json"
JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()


def default_project() -> Dict:
    return {
        "version": 3,
        "restaurant": {"name": "Restaurant Lab", "layoutImage": None},
        "tables": [],
        "zones": [],
        "cameras": [],
        "cameraLinks": [],
        "manualMarks": [],
        "staff": [],
        "managerSettings": {
            "greetingTargetSeconds": 90,
            "longSessionMinutes": 90,
            "minimumVisitsPerSession": 1,
            "anonymousRetentionDays": 7,
        },
        "settings": {
            "sampleFps": 3,
            "tableRadius": 0.075,
            "handoffDistance": 0.12,
            "handoffSeconds": 8,
            "bodyReidThreshold": 0.58,
            "staffFaceThreshold": 0.42,
            "customerFaceThreshold": 0.43,
        },
    }


class ProjectStore:
    lock = threading.RLock()

    @classmethod
    def load(cls) -> Dict:
        with cls.lock:
            if not PROJECT_PATH.exists():
                project = default_project()
                cls.save(project)
                return project
            with PROJECT_PATH.open("r", encoding="utf-8") as handle:
                project = json.load(handle)
            defaults = default_project()
            for key, value in defaults.items():
                project.setdefault(key, value)
            project["version"] = max(int(project.get("version", 1)), 3)
            for key, value in defaults["settings"].items():
                project["settings"].setdefault(key, value)
            for camera in project.get("cameras", []):
                camera.setdefault("calibration", [])
                camera["calibrationQuality"] = calibration_quality(camera)
                for key in ("videoPath", "previewPath"):
                    if camera.get(key):
                        camera[key] = str(camera[key]).replace("\\", "/")
            for person in project.get("staff", []):
                person.setdefault("role", "waiter")
                if person.get("photoPath"):
                    person["photoPath"] = str(person["photoPath"]).replace("\\", "/")
            for key, value in defaults["managerSettings"].items():
                project["managerSettings"].setdefault(key, value)
            return project

    @classmethod
    def save(cls, project: Dict) -> None:
        with cls.lock:
            for camera in project.get("cameras", []):
                camera["calibrationQuality"] = calibration_quality(camera)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            temporary = PROJECT_PATH.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(project, handle, indent=2)
            temporary.replace(PROJECT_PATH)


def json_bytes(value: Dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def public_project(project: Dict) -> Dict:
    """Return project configuration without exposing stored biometric vectors."""
    visible = copy.deepcopy(project)
    for person in visible.get("staff", []):
        person.pop("embedding", None)
    return visible


def public_results(results: Dict) -> Dict:
    """Return review data without face or body feature vectors."""
    visible = copy.deepcopy(results)
    for tracklet in visible.get("tracklets", []):
        tracklet.pop("embedding", None)
        tracklet.pop("appearance", None)
    return visible


def preserve_staff_embeddings(incoming: Dict, stored: Dict) -> Dict:
    """Keep server-side embeddings when the UI saves non-sensitive project edits."""
    existing = {person.get("id"): person for person in stored.get("staff", [])}
    for person in incoming.get("staff", []):
        saved = existing.get(person.get("id"))
        if saved and saved.get("embedding"):
            person["embedding"] = saved["embedding"]
    return incoming


def relative_to_root(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def calibration_quality(camera: Dict) -> Dict:
    pairs = camera.get("calibration", [])
    if len(pairs) < 4:
        return {"ready": False, "label": "Incomplete", "score": 0, "points": len(pairs), "error": None, "coverage": 0}
    source = np.asarray([pair["image"] for pair in pairs], dtype=np.float32)
    target = np.asarray([pair["map"] for pair in pairs], dtype=np.float32)
    matrix, _ = cv2.findHomography(source, target, cv2.RANSAC, 0.025)
    if matrix is None:
        return {"ready": False, "label": "Invalid", "score": 0, "points": len(pairs), "error": None, "coverage": 0}
    projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    error = float(np.sqrt(np.mean(np.sum((projected - target) ** 2, axis=1))))
    source_area = float(cv2.contourArea(cv2.convexHull(source)))
    target_area = float(cv2.contourArea(cv2.convexHull(target)))
    coverage = min(1.0, (math.sqrt(max(source_area, 0) / 0.2) + math.sqrt(max(target_area, 0) / 0.2)) / 2)
    error_score = max(0.0, 1.0 - error / 0.05)
    point_score = min(1.0, len(pairs) / 6.0)
    score = round(55 * error_score + 25 * coverage + 20 * point_score)
    label = "Good" if score >= 80 else "Fair" if score >= 60 else "Weak"
    return {"ready": True, "label": label, "score": score, "points": len(pairs), "error": round(error, 5), "coverage": round(coverage, 3)}


def save_json_atomic(path: Path, value: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle)
    temporary.replace(path)


class TaskStore:
    lock = threading.RLock()
    roles = {"waiter", "runner", "busser", "host", "captain", "manager"}
    actions = {"accept", "complete", "handoff", "dismiss"}

    @classmethod
    def load(cls) -> Dict:
        with cls.lock:
            if not TASKS_PATH.exists():
                return {"version": 1, "tasks": []}
            with TASKS_PATH.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            value.setdefault("version", 1)
            value.setdefault("tasks", [])
            return value

    @classmethod
    def save(cls, value: Dict) -> None:
        with cls.lock:
            save_json_atomic(TASKS_PATH, value)

    @classmethod
    def generate_for_run(cls, results: Dict, project: Dict) -> int:
        with cls.lock:
            value = cls.load()
            run_id = results.get("runId")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for task in value["tasks"]:
                if task.get("source") == "analysis" and task.get("status") in {"open", "accepted"}:
                    task["status"] = "superseded"
                    task.setdefault("history", []).append({"action": "supersede", "at": now})

            created = []
            greeting_target = float(project.get("managerSettings", {}).get("greetingTargetSeconds", 90))
            confidence = float(results.get("summary", {}).get("averageDetectionConfidence", 0))
            for session in results.get("sessions", []):
                table_number = session.get("tableNumber")
                common = {
                    "tableId": session.get("tableId"),
                    "tableNumber": table_number,
                    "zoneId": None,
                    "source": "analysis",
                    "sourceRunId": run_id,
                    "confidence": round(confidence, 3),
                    "status": "open",
                    "assignedTo": None,
                    "createdAt": now,
                    "history": [{"action": "create", "at": now, "actorId": None}],
                }
                first_service = session.get("firstServiceSeconds")
                if first_service is None or float(first_service) > greeting_target:
                    event_time = float(session.get("start", 0)) + greeting_target
                    created.append({
                        **common,
                        "id": uuid.uuid4().hex[:12],
                        "kind": "acknowledgement",
                        "priority": "critical",
                        "targetRole": "waiter",
                        "title": f"Acknowledge Table {table_number}",
                        "detail": "No staff acknowledgement was detected" if first_service is None else f"First service exceeded the {round(greeting_target)} second target",
                        "eventTimeSeconds": round(event_time, 2),
                        "requestedAction": "Check in with the table now",
                    })
                if int(session.get("staffVisits", 0)) == 0:
                    created.append({
                        **common,
                        "id": uuid.uuid4().hex[:12],
                        "kind": "service_request",
                        "priority": "high",
                        "targetRole": "captain",
                        "title": f"Resolve coverage at Table {table_number}",
                        "detail": "The occupied session ended without a detected staff visit",
                        "eventTimeSeconds": round(float(session.get("end", 0)), 2),
                        "requestedAction": "Assign a named person and verify the guest need",
                    })
                created.append({
                    **common,
                    "id": uuid.uuid4().hex[:12],
                    "kind": "reset",
                    "priority": "normal",
                    "targetRole": "busser",
                    "title": f"Clear and reset Table {table_number}",
                    "detail": "The occupied session ended and the table may need resetting",
                    "eventTimeSeconds": round(float(session.get("end", 0)), 2),
                    "requestedAction": "Confirm the table is clean and ready",
                })
            value["tasks"].extend(created)
            cls.save(value)
            return len(created)

    @classmethod
    def create_manual(cls, request: Dict, project: Dict) -> Dict:
        with cls.lock:
            return cls._create_manual_locked(request, project)

    @classmethod
    def _create_manual_locked(cls, request: Dict, project: Dict) -> Dict:
        title = str(request.get("title", "")).strip()[:100]
        requested_action = str(request.get("requestedAction", "")).strip()[:140]
        target_role = request.get("targetRole")
        if not title or not requested_action or target_role not in cls.roles:
            raise ValueError("Enter a task, requested action and valid target role")
        created_by = request.get("createdBy")
        if created_by and not any(person.get("id") == created_by for person in project.get("staff", [])):
            raise ValueError("Choose a valid staff actor")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        table = next((item for item in project.get("tables", []) if item.get("id") == request.get("tableId")), None)
        zone = next((item for item in project.get("zones", []) if item.get("id") == request.get("zoneId")), None)
        task = {
            "id": uuid.uuid4().hex[:12],
            "kind": "manual",
            "priority": request.get("priority") if request.get("priority") in {"normal", "high", "critical"} else "normal",
            "targetRole": target_role,
            "title": title,
            "detail": str(request.get("detail", "")).strip()[:240],
            "requestedAction": requested_action,
            "tableId": table.get("id") if table else None,
            "tableNumber": table.get("number") if table else None,
            "zoneId": zone.get("id") if zone else None,
            "source": "manual",
            "sourceRunId": None,
            "confidence": None,
            "status": "open",
            "assignedTo": None,
            "createdAt": now,
            "eventTimeSeconds": None,
            "history": [{"action": "create", "at": now, "actorId": created_by}],
        }
        value = cls.load()
        value["tasks"].append(task)
        cls.save(value)
        return task

    @classmethod
    def act(cls, request: Dict, project: Dict) -> Dict:
        with cls.lock:
            return cls._act_locked(request, project)

    @classmethod
    def _act_locked(cls, request: Dict, project: Dict) -> Dict:
        task_id, action = request.get("taskId"), request.get("action")
        actor_id = request.get("actorId")
        if not task_id or action not in cls.actions:
            raise ValueError("Choose a task and valid action")
        staff = {person.get("id"): person for person in project.get("staff", [])}
        if actor_id not in staff:
            raise ValueError("Choose an enrolled staff actor")
        value = cls.load()
        task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
        if task is None:
            raise ValueError("Task was not found")
        if task.get("status") in {"completed", "dismissed", "superseded"}:
            raise ValueError("This task is already closed")
        person = staff[actor_id]
        elevated = person.get("role") in {"captain", "manager"}
        if task.get("assignedTo") and task.get("assignedTo") != actor_id and not elevated:
            raise ValueError("This action is assigned to someone else")
        if not task.get("assignedTo") and task.get("targetRole") != person.get("role") and not elevated:
            raise ValueError("This action is routed to a different role")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {"action": action, "at": now, "actorId": actor_id}
        if action == "accept":
            task["status"] = "accepted"
            task["assignedTo"] = actor_id
        elif action == "complete":
            task["status"] = "completed"
            task["assignedTo"] = actor_id
        elif action == "handoff":
            target_id = request.get("targetStaffId")
            target_role = request.get("targetRole")
            if target_id and target_id not in staff:
                raise ValueError("Choose a valid handoff person")
            if target_role and target_role not in cls.roles:
                raise ValueError("Choose a valid handoff role")
            if not target_id and not target_role:
                raise ValueError("Choose who should receive the handoff")
            task["status"] = "open"
            task["assignedTo"] = target_id or None
            if target_role:
                task["targetRole"] = target_role
            entry.update({"targetStaffId": target_id, "targetRole": target_role})
        else:
            task["status"] = "dismissed"
            task["assignedTo"] = actor_id
            entry["reason"] = str(request.get("reason", "Not actionable")).strip()[:240]
        task.setdefault("history", []).append(entry)
        cls.save(value)
        return task


def rebuilt_results(project: Dict, previous: Dict) -> Dict:
    frame_repairs = enforce_same_frame_exclusivity(previous.get("observations", []))
    if frame_repairs["count"]:
        consistency = previous.setdefault("identityConsistency", {})
        consistency["sameFrameIdentitiesSplit"] = int(consistency.get("sameFrameIdentitiesSplit", 0)) + frame_repairs["count"]
        consistency["examples"] = (consistency.get("examples", []) + frame_repairs["examples"])[:100]
    analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
    analyzer.project = project
    analyzer.sample_fps = max(0.5, min(float(project.get("settings", {}).get("sampleFps", 3)), 10.0))
    rebuilt = analyzer._build_results(
        previous.get("observations", []),
        previous.get("tracklets", []),
        previous.get("handoffs", []),
    )
    rebuilt["runId"] = previous.get("runId")
    rebuilt["sourceGeneratedAt"] = previous.get("sourceGeneratedAt", previous.get("generatedAt"))
    rebuilt["corrections"] = previous.get("corrections", [])
    for key in ("timestampSync", "spatialSync", "autoCalibration", "identityConsistency", "runtime"):
        if key in previous:
            rebuilt[key] = previous[key]
    return rebuilt


def apply_result_correction(project: Dict, results: Dict, request: Dict) -> Dict:
    action = request.get("action")
    observations = results.get("observations", [])
    tracklets = results.get("tracklets", [])
    handoffs = results.get("handoffs", [])

    def replace_identity(source: str, target: str) -> int:
        changed = 0
        target_role = "staff" if target.startswith("staff:") else "customer"
        for observation in observations:
            if observation.get("globalId") == source:
                observation["globalId"] = target
                observation["role"] = target_role
                changed += 1
        for tracklet in tracklets:
            if tracklet.get("globalId") == source:
                tracklet["globalId"] = target
                tracklet["role"] = target_role
        for handoff in handoffs:
            for key in ("globalId", "sourceId", "targetId"):
                if handoff.get(key) == source:
                    handoff[key] = target
        return changed

    details: Dict = {}
    if action == "merge":
        source, target = request.get("sourceId"), request.get("targetId")
        if not source or not target or source == target:
            raise ValueError("Choose two different identities to merge")
        changed = replace_identity(source, target)
        if not changed:
            raise ValueError("The source identity was not found")
        details = {"sourceId": source, "targetId": target, "observationsChanged": changed}
    elif action == "relabel":
        identity = request.get("identityId")
        role = request.get("role")
        if not identity or role not in {"staff", "customer"}:
            raise ValueError("Choose an identity and its corrected role")
        if role == "staff":
            staff_id = request.get("staffId")
            if not any(entry.get("id") == staff_id for entry in project.get("staff", [])):
                raise ValueError("Choose an enrolled staff profile")
            target = f"staff:{staff_id}"
        else:
            target = identity if identity.startswith("customer:") else next_customer_id(results)
        changed = replace_identity(identity, target)
        if not changed:
            raise ValueError("The identity was not found")
        details = {"identityId": identity, "targetId": target, "role": role, "observationsChanged": changed}
    elif action == "split":
        identity, camera_id = request.get("identityId"), request.get("cameraId")
        split_time = float(request.get("time", -1))
        if not identity or not camera_id or split_time < 0:
            raise ValueError("Choose an identity, camera and valid split time")
        target = next_customer_id(results)
        changed = 0
        for observation in observations:
            if observation.get("globalId") == identity and observation.get("cameraId") == camera_id and float(observation.get("time", 0)) >= split_time:
                observation["globalId"] = target
                observation["role"] = "customer"
                changed += 1
        for tracklet in tracklets:
            if tracklet.get("globalId") == identity and tracklet.get("cameraId") == camera_id and float(tracklet.get("firstSeen", 0)) >= split_time:
                tracklet["globalId"] = target
                tracklet["role"] = "customer"
        if not changed:
            raise ValueError("No observations were found after that split point")
        details = {"identityId": identity, "newIdentityId": target, "cameraId": camera_id, "time": split_time, "observationsChanged": changed}
    else:
        raise ValueError("Unknown correction action")

    results["observations"] = observations
    results["tracklets"] = tracklets
    results["handoffs"] = handoffs
    corrections = list(results.get("corrections", []))
    corrections.append(
        {
            "id": uuid.uuid4().hex[:12],
            "action": action,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **details,
        }
    )
    results["corrections"] = corrections
    return rebuilt_results(project, results)


def next_customer_id(results: Dict) -> str:
    numbers = []
    for observation in results.get("observations", []):
        identity = str(observation.get("globalId", ""))
        if identity.startswith("customer:"):
            try:
                numbers.append(int(identity.split(":", 1)[1]))
            except ValueError:
                pass
    return f"customer:{(max(numbers) if numbers else 0) + 1:03d}"


class RestaurantVisionHandler(BaseHTTPRequestHandler):
    server_version = "RestaurantVision/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self) -> None:
        try:
            route = urlparse(self.path)
            if route.path == "/api/project":
                return self.send_json(public_project(ProjectStore.load()))
            if route.path == "/api/results":
                params = parse_qs(route.query)
                run_id = params.get("runId", [None])[0]
                result_path = RUNS_DIR / f"{run_id}.json" if run_id and run_id.replace("-", "").isalnum() else RESULTS_PATH
                if not result_path.exists():
                    return self.send_json({"available": False})
                with result_path.open("r", encoding="utf-8") as handle:
                    return self.send_json({"available": True, "results": public_results(json.load(handle))})
            if route.path == "/api/runs":
                return self.send_json({"runs": self.list_runs()})
            if route.path == "/api/tasks":
                return self.send_json(TaskStore.load())
            if route.path == "/api/health":
                return self.send_json({"ok": True, "models": self.model_status()})
            if route.path.startswith("/api/jobs/"):
                job_id = route.path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if job is None:
                    return self.send_json({"error": "Unknown job"}, HTTPStatus.NOT_FOUND)
                return self.send_json(job)
            if route.path == "/api/frame":
                params = parse_qs(route.query)
                return self.send_camera_frame(params.get("cameraId", [""])[0])
            if route.path.startswith("/media/"):
                return self.send_media(route.path[len("/media/") :])
            return self.send_static(route.path)
        except Exception as error:
            self.handle_error(error)

    def do_POST(self) -> None:
        try:
            route = urlparse(self.path)
            if route.path == "/api/project":
                project = self.read_json()
                self.validate_project(project)
                project = preserve_staff_embeddings(project, ProjectStore.load())
                ProjectStore.save(project)
                return self.send_json(public_project(project))
            if route.path == "/api/cameras":
                return self.add_camera()
            if route.path == "/api/staff":
                return self.add_staff()
            if route.path == "/api/remove":
                return self.remove_item()
            if route.path == "/api/analyze":
                return self.start_analysis()
            if route.path == "/api/timestamps/sync":
                project = ProjectStore.load()
                report = synchronize_project_cameras(ROOT, project)
                if report.get("status") == "verified":
                    if report.get("applied"):
                        project["timestampSyncUpdatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    ProjectStore.save(project)
                return self.send_json({"project": public_project(project), "timestampSync": report})
            if route.path == "/api/corrections":
                return self.correct_results()
            if route.path == "/api/tasks":
                task = TaskStore.create_manual(self.read_json(), ProjectStore.load())
                return self.send_json({"task": task, **TaskStore.load()}, HTTPStatus.CREATED)
            if route.path == "/api/tasks/actions":
                task = TaskStore.act(self.read_json(), ProjectStore.load())
                return self.send_json({"task": task, **TaskStore.load()})
            if route.path == "/api/privacy/prune":
                return self.prune_expired_results()
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.handle_error(error)

    def read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 25_000_000:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length))

    def multipart(self) -> cgi.FieldStorage:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("Expected a multipart upload")
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )

    @staticmethod
    def field_text(form: cgi.FieldStorage, name: str, default: str = "") -> str:
        field = form[name] if name in form else None
        return str(field.value) if field is not None else default

    def add_camera(self) -> None:
        form = self.multipart()
        upload = form["video"] if "video" in form else None
        if upload is None or not getattr(upload, "file", None):
            raise ValueError("Choose a video file")
        suffix = Path(upload.filename or "camera.mp4").suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}:
            raise ValueError("Supported video formats: MP4, MOV, M4V, AVI, MKV and WebM")

        camera_id = uuid.uuid4().hex[:10]
        video_path = DATA_DIR / "cameras" / f"{camera_id}{suffix}"
        preview_path = DATA_DIR / "cameras" / f"{camera_id}.jpg"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        with video_path.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle, length=1024 * 1024)

        capture = cv2.VideoCapture(str(video_path))
        ok, frame = capture.read()
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()
        if not ok:
            video_path.unlink(missing_ok=True)
            raise ValueError("The uploaded file could not be read as a video")
        cv2.imwrite(str(preview_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])

        camera = {
            "id": camera_id,
            "name": self.field_text(form, "name", f"Camera {camera_id[:4]}").strip(),
            "videoPath": relative_to_root(video_path),
            "previewPath": relative_to_root(preview_path),
            "timeOffsetSeconds": float(self.field_text(form, "timeOffsetSeconds", "0") or 0),
            "calibration": [],
            "metadata": {
                "fps": round(fps, 3),
                "frames": frame_count,
                "durationSeconds": round(frame_count / fps, 2) if fps else 0,
                "width": width,
                "height": height,
            },
        }
        project = ProjectStore.load()
        project["cameras"].append(camera)
        ProjectStore.save(project)
        self.send_json({"camera": camera, "project": public_project(project)}, HTTPStatus.CREATED)

    def add_staff(self) -> None:
        form = self.multipart()
        upload = form["photo"] if "photo" in form else None
        name = self.field_text(form, "name").strip()
        if not name:
            raise ValueError("Enter the staff member's name")
        staff_id = uuid.uuid4().hex[:10]
        role = self.field_text(form, "role", "waiter").strip().lower()
        if role not in TaskStore.roles:
            raise ValueError("Choose a valid staff role")
        entry = {"id": staff_id, "name": name, "role": role, "embedding": None, "photoPath": None}
        if upload is not None and getattr(upload, "file", None) and upload.filename:
            payload = upload.file.read()
            image = cv2.imdecode(__import__("numpy").frombuffer(payload, dtype="uint8"), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("The staff photo could not be read")
            models = VisionModels(ROOT / "models")
            entry["embedding"] = models.embedding_from_image(image)
            suffix = Path(upload.filename or "staff.jpg").suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                suffix = ".jpg"
            photo_path = DATA_DIR / "staff" / f"{staff_id}{suffix}"
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            with photo_path.open("wb") as handle:
                handle.write(payload)
            entry["photoPath"] = relative_to_root(photo_path)
        project = ProjectStore.load()
        project["staff"].append(entry)
        ProjectStore.save(project)
        public_entry = {key: value for key, value in entry.items() if key != "embedding"}
        self.send_json({"staff": public_entry, "project": public_project(project)}, HTTPStatus.CREATED)

    def remove_item(self) -> None:
        request = self.read_json()
        item_type, item_id = request.get("type"), request.get("id")
        if item_type not in {"camera", "staff", "table", "zone"} or not item_id:
            raise ValueError("Invalid removal request")
        collection_name = {"camera": "cameras", "staff": "staff", "table": "tables", "zone": "zones"}[item_type]
        project = ProjectStore.load()
        items = project[collection_name]
        target = next((item for item in items if item.get("id") == item_id), None)
        if target is None:
            raise ValueError("Item was not found")
        project[collection_name] = [item for item in items if item.get("id") != item_id]
        if item_type == "camera":
            project["cameraLinks"] = [
                link for link in project.get("cameraLinks", [])
                if link.get("fromCameraId") != item_id and link.get("toCameraId") != item_id
            ]
            project["manualMarks"] = [mark for mark in project.get("manualMarks", []) if mark.get("cameraId") != item_id]
        elif item_type == "staff":
            project["manualMarks"] = [mark for mark in project.get("manualMarks", []) if mark.get("staffId") != item_id]
        elif item_type == "table":
            for mark in project.get("manualMarks", []):
                if mark.get("tableId") == item_id:
                    mark["tableId"] = None
        elif item_type == "zone":
            for link in project.get("cameraLinks", []):
                if link.get("exitZoneId") == item_id:
                    link["exitZoneId"] = None
                if link.get("entryZoneId") == item_id:
                    link["entryZoneId"] = None
        ProjectStore.save(project)
        if item_type == "camera":
            for key in ("videoPath", "previewPath"):
                if target.get(key):
                    (ROOT / target[key]).unlink(missing_ok=True)
        elif item_type == "staff" and target.get("photoPath"):
            (ROOT / target["photoPath"]).unlink(missing_ok=True)
        self.send_json(public_project(project))

    def start_analysis(self) -> None:
        project = ProjectStore.load()
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "status": "queued", "progress": 0.0, "message": "Preparing models"}
        with JOBS_LOCK:
            JOBS[job_id] = job

        def run() -> None:
            def report(progress: float, message: str) -> None:
                with JOBS_LOCK:
                    job.update({"status": "running", "progress": round(progress, 4), "message": message})

            try:
                report(0.0, "Reading embedded camera timestamps")
                timestamp_report = synchronize_project_cameras(ROOT, project)
                if timestamp_report.get("status") == "verified":
                    if timestamp_report.get("applied"):
                        project["timestampSyncUpdatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    ProjectStore.save(project)
                analyzer = VideoAnalyzer(ROOT, project)
                results = analyzer.analyze(report)
                results["timestampSync"] = timestamp_report
                run_id = f"run-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{job_id[:5]}"
                results["runId"] = run_id
                results["corrections"] = []
                save_json_atomic(RESULTS_PATH, results)
                save_json_atomic(RUNS_DIR / f"{run_id}.json", results)
                task_count = TaskStore.generate_for_run(results, project)
                backend = results.get("runtime", {}).get("personDetector", "CPU")
                with JOBS_LOCK:
                    job.update({"status": "complete", "progress": 1.0, "message": f"Analysis complete on {backend} · {task_count} actions prepared"})
            except Exception as error:
                traceback.print_exc()
                with JOBS_LOCK:
                    job.update({"status": "error", "message": str(error), "error": type(error).__name__})

        threading.Thread(target=run, name=f"analysis-{job_id}", daemon=True).start()
        self.send_json(job, HTTPStatus.ACCEPTED)

    def correct_results(self) -> None:
        if not RESULTS_PATH.exists():
            raise ValueError("Run an analysis before making corrections")
        request = self.read_json()
        with RESULTS_PATH.open("r", encoding="utf-8") as handle:
            results = json.load(handle)
        corrected = apply_result_correction(ProjectStore.load(), results, request)
        save_json_atomic(RESULTS_PATH, corrected)
        if corrected.get("runId"):
            save_json_atomic(RUNS_DIR / f"{corrected['runId']}.json", corrected)
        self.send_json({"results": public_results(corrected), "correction": corrected.get("corrections", [])[-1]})

    def prune_expired_results(self) -> None:
        request = self.read_json()
        configured = ProjectStore.load().get("managerSettings", {}).get("anonymousRetentionDays", 7)
        days = max(1, min(int(request.get("days", configured)), 365))
        cutoff = time.time() - days * 86400
        removed = []

        def expired(value: Dict) -> bool:
            generated = value.get("sourceGeneratedAt") or value.get("generatedAt")
            if not generated:
                return False
            try:
                return calendar.timegm(time.strptime(generated, "%Y-%m-%dT%H:%M:%SZ")) < cutoff
            except ValueError:
                return False

        removed_run_ids = set()
        for path in RUNS_DIR.glob("run-*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    value = json.load(handle)
                if expired(value):
                    path.unlink()
                    removed.append(path.name)
                    if value.get("runId"):
                        removed_run_ids.add(value["runId"])
            except (OSError, json.JSONDecodeError):
                continue
        if RESULTS_PATH.exists():
            try:
                with RESULTS_PATH.open("r", encoding="utf-8") as handle:
                    latest = json.load(handle)
                if expired(latest):
                    RESULTS_PATH.unlink()
                    removed.append(RESULTS_PATH.name)
            except (OSError, json.JSONDecodeError):
                pass
        if removed_run_ids and TASKS_PATH.exists():
            task_value = TaskStore.load()
            before = len(task_value["tasks"])
            task_value["tasks"] = [task for task in task_value["tasks"] if task.get("sourceRunId") not in removed_run_ids]
            if len(task_value["tasks"]) != before:
                TaskStore.save(task_value)
        self.send_json({"removed": removed, "retentionDays": days})

    @staticmethod
    def list_runs() -> list:
        if not RUNS_DIR.exists():
            return []
        rows = []
        for path in sorted(RUNS_DIR.glob("run-*.json"), reverse=True):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    result = json.load(handle)
                rows.append(
                    {
                        "id": result.get("runId", path.stem),
                        "generatedAt": result.get("generatedAt"),
                        "summary": result.get("summary", {}),
                        "corrections": len(result.get("corrections", [])),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return rows[:50]

    def send_camera_frame(self, camera_id: str) -> None:
        camera = next((item for item in ProjectStore.load()["cameras"] if item["id"] == camera_id), None)
        if camera is None:
            return self.send_json({"error": "Camera not found"}, HTTPStatus.NOT_FOUND)
        return self.send_file(ROOT / camera["previewPath"], cache=False)

    def send_media(self, relative: str) -> None:
        rel = unquote(relative).replace("\\", "/")
        if rel.startswith("data/"):
            rel = rel[len("data/") :]
        candidate = (DATA_DIR / rel).resolve()
        if DATA_DIR.resolve() not in candidate.parents or not candidate.is_file():
            return self.send_json({"error": "Media not found"}, HTTPStatus.NOT_FOUND)
        return self.send_file(candidate, cache=True)

    def send_static(self, route: str) -> None:
        relative = "index.html" if route in {"", "/"} else unquote(route.lstrip("/"))
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in candidate.parents or not candidate.is_file():
            candidate = STATIC_DIR / "index.html"
        self.send_file(candidate, cache=False)

    def send_file(self, path: Path, cache: bool) -> None:
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = HTTPStatus.OK
        if range_header and range_header.startswith("bytes="):
            value = range_header[6:].split(",", 1)[0]
            start_text, end_text = value.split("-", 1)
            if not start_text and end_text:
                suffix_length = max(1, min(int(end_text), size))
                start = size - suffix_length
            elif start_text:
                start = max(0, min(int(start_text), size - 1))
            if end_text and start_text:
                end = max(start, min(int(end_text), size - 1))
            status = HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def send_json(self, value: Dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def handle_error(self, error: Exception) -> None:
        status = HTTPStatus.BAD_REQUEST if isinstance(error, (ValueError, json.JSONDecodeError)) else HTTPStatus.INTERNAL_SERVER_ERROR
        if status == HTTPStatus.INTERNAL_SERVER_ERROR:
            traceback.print_exc()
        self.send_json({"error": str(error), "type": type(error).__name__}, status)

    @staticmethod
    def validate_project(project: Dict) -> None:
        if not isinstance(project, dict):
            raise ValueError("Project must be an object")
        for key in ("restaurant", "tables", "zones", "cameras", "cameraLinks", "staff", "settings"):
            if key not in project:
                raise ValueError(f"Project is missing {key}")
        if len(project["cameras"]) > 32 or len(project["cameraLinks"]) > 128 or len(project["tables"]) > 200 or len(project["zones"]) > 500:
            raise ValueError("Project exceeds the prototype limit")
        camera_ids = {camera.get("id") for camera in project["cameras"]}
        camera_by_id = {camera.get("id"): camera for camera in project["cameras"]}
        for camera in project["cameras"]:
            try:
                offset = float(camera.get("timeOffsetSeconds", 0))
                adjustment = float(camera.get("manualTimeAdjustmentSeconds", 0))
            except (TypeError, ValueError):
                raise ValueError("Camera time adjustments must be numeric")
            if not math.isfinite(offset) or not math.isfinite(adjustment) or abs(adjustment) > 30:
                raise ValueError("Camera time adjustments must be within 30 seconds")
        staff_ids = {person.get("id") for person in project["staff"]}
        table_ids = {table.get("id") for table in project["tables"]}
        marks = project.get("manualMarks", [])
        if not isinstance(marks, list) or len(marks) > 1000:
            raise ValueError("Too many manual person markings")
        mark_ids = set()
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get("id"), str) or not mark["id"] or mark["id"] in mark_ids:
                raise ValueError("Manual person markings need unique IDs")
            mark_ids.add(mark["id"])
            camera = camera_by_id.get(mark.get("cameraId"))
            if camera is None:
                raise ValueError("A manual marking references a missing camera")
            if mark.get("role") not in {"customer", "staff"}:
                raise ValueError("A manual marking must be customer or staff")
            if mark["role"] == "staff" and mark.get("staffId") not in staff_ids:
                raise ValueError("Choose an enrolled staff actor for staff markings")
            if mark.get("tableId") and mark["tableId"] not in table_ids:
                raise ValueError("A manual marking references a missing table")
            box = mark.get("bbox")
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError("Draw a person box before saving a manual marking")
            try:
                x, y, width, height = [float(value) for value in box]
                start, end = float(mark["startSeconds"]), float(mark["endSeconds"])
            except (TypeError, ValueError, KeyError):
                raise ValueError("Manual marking position and time must be numeric")
            duration = float(camera.get("metadata", {}).get("durationSeconds") or 0)
            if not all(map(math.isfinite, (x, y, width, height, start, end))) or x < 0 or y < 0 or width < .005 or height < .005 or x + width > 1.001 or y + height > 1.001 or start < 0 or end <= start or (duration and end > duration + 0.5):
                raise ValueError("Manual marking box or time is outside the camera recording")
        zone_ids = {zone.get("id") for zone in project["zones"]}
        for link in project["cameraLinks"]:
            if link.get("fromCameraId") not in camera_ids or link.get("toCameraId") not in camera_ids:
                raise ValueError("Every camera route must reference two existing cameras")
            if link.get("fromCameraId") == link.get("toCameraId"):
                raise ValueError("A camera route must connect two different cameras")
            if link.get("mode") not in {"handoff", "overlap"}:
                raise ValueError("Camera route mode must be handoff or overlap")
            if link.get("exitZoneId") and link.get("exitZoneId") not in zone_ids:
                raise ValueError("Camera route exit zone was not found")
            if link.get("entryZoneId") and link.get("entryZoneId") not in zone_ids:
                raise ValueError("Camera route entry zone was not found")
            minimum = float(link.get("minSeconds", 0))
            maximum = float(link.get("maxSeconds", 8))
            distance = float(link.get("maxDistance", 0.12))
            if maximum < minimum or maximum > 120 or minimum < -10 or distance <= 0 or distance > 1.5:
                raise ValueError("Camera route timing or distance is outside the supported range")

    @staticmethod
    def model_status() -> Dict[str, bool]:
        names = (
            "face_detection_yunet_2023mar.onnx",
            "face_recognition_sface_2021dec.onnx",
            "object_detection_yolox_2022nov.onnx",
            "person-reidentification-retail-0288.xml",
            "person-reidentification-retail-0288.bin",
        )
        return {name: (ROOT / "models" / name).exists() for name in names}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Restaurant Vision locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the app in the default browser")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ProjectStore.load()
    server = ThreadingHTTPServer((args.host, args.port), RestaurantVisionHandler)
    print(f"Restaurant Vision is running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Restaurant Vision.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
