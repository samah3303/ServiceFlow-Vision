# Uncommitted Changes & Fixes Reference Guide

This document captures all uncommitted bug fixes, reliability improvements, and optimizations made to the ServiceFlow Vision project across **6 files**.

Use this guide to easily review or re-apply these changes to a fresh repository or another branch.

---

## Table of Contents
1. [Quick Automated Apply (Git Patch)](#1-quick-automated-apply-git-patch)
2. [Summary of Modified Files](#2-summary-of-modified-files)
3. [File-by-File Changes & Diffs](#3-file-by-file-changes--diffs)
   - [A. server.py](#a-serverpy)
   - [B. app/analytics.py](#b-appanalyticspy)
   - [C. app/tracking.py](#c-apptrackingpy)
   - [D. app/geometry.py](#d-appgeometrypy)
   - [E. app/vision.py](#e-appvisionpy)
   - [F. static/app.js](#f-staticappjs)
4. [Untracked Temporary Files](#4-untracked-temporary-files)

---

## 1. Quick Automated Apply (Git Patch)

A git patch file named `uncommitted_fixes.patch` has been generated in the project root directory.

To apply all changes in one command to a new clone or repository tracking the same base commit:

```bash
git apply uncommitted_fixes.patch
```

If you ever need to inspect what will be applied without touching files:
```bash
git apply --stat uncommitted_fixes.patch
git apply --check uncommitted_fixes.patch
```

---

## 2. Summary of Modified Files

| File | Type | Changes / Fixes |
|---|---|---|
| `server.py` | Backend Server | Replaced deprecated `cgi` module with `email.parser` (Python 3.13 compatibility), added `JOBS_LOCK` guard against concurrent analysis runs, added `RESULTS_LOCK` for atomic result writes/reads, fixed split correction handoffs. |
| `app/analytics.py` | Analytics Engine | Prevented transitive merging of two different staff members, added 20-sample cap to customer embeddings to prevent memory leaks, prevented premature customer lockouts, added dwell-time filter for table party size (>= 15s), fixed staff visit detection during occlusion gaps. |
| `app/tracking.py` | Tracker | Clamped predicted bounding box width and height to `>= 1.0` (prevents negative dimensions and corrupt IoU), added per-frame caching for embeddings and predicted boxes to speed up ByteTracker. |
| `app/geometry.py` | Coordinate Mapping | Clamped projected map coordinates to `[-0.5, 1.5]` to avoid math crashes on horizon points. |
| `app/vision.py` | Vision Pipeline | Preserved highest-confidence face instead of blindly overwriting in multi-face person bounding boxes. |
| `static/app.js` | Frontend SPA | Added cleanup for `<video>` elements in the review tab to prevent media/decoder leaks, added an 8-hour timeout and `finally` cleanup block to `pollJob()`. |

---

## 3. File-by-File Changes & Diffs

### A. `server.py`

#### 1. Replaced `cgi` and added `RESULTS_LOCK`
- **Why**: Python 3.13 completely removed the built-in `cgi` module (`PEP 594`), which broke multipart uploads. Also added `RESULTS_LOCK` to eliminate file-write race conditions.
- **Diff**:
```diff
@@ -3,7 +3,6 @@ from __future__ import annotations
 
 import argparse
 import calendar
-import cgi
 import copy
 import json
 import math
@@ -37,6 +36,7 @@ RUNS_DIR = DATA_DIR / "runs"
 TASKS_PATH = DATA_DIR / "tasks.json"
 JOBS: Dict[str, Dict] = {}
 JOBS_LOCK = threading.Lock()
+RESULTS_LOCK = threading.RLock()
```

#### 2. Updated `apply_result_correction()` for split handoffs
- **Why**: When an identity was split at a timestamp, observations and tracklets were updated but handoffs were missed, leaving stale identity handoffs.
- **Diff**:
```diff
@@ -439,6 +439,9 @@ def apply_result_correction(project: Dict, results: Dict, request: Dict) -> Dict
             if tracklet.get("globalId") == identity and tracklet.get("cameraId") == camera_id and float(tracklet.get("firstSeen", 0)) >= split_time:
                 tracklet["globalId"] = target
                 tracklet["role"] = "customer"
+        for handoff in handoffs:
+            if handoff.get("globalId") == identity and float(handoff.get("time", 0)) > split_time:
+                handoff["globalId"] = target
         if not changed:
             raise ValueError("No observations were found after that split point")
```

#### 3. Wrapped `/api/results` read with `RESULTS_LOCK`
- **Diff**:
```diff
@@ -490,8 +493,14 @@ class RestaurantVisionHandler(BaseHTTPRequestHandler):
                 result_path = RUNS_DIR / f"{run_id}.json" if run_id and run_id.replace("-", "").isalnum() else RESULTS_PATH
                 if not result_path.exists():
                     return self.send_json({"available": False})
-                with result_path.open("r", encoding="utf-8") as handle:
-                    return self.send_json({"available": True, "results": public_results(json.load(handle))})
+                if result_path == RESULTS_PATH:
+                    with RESULTS_LOCK:
+                        with result_path.open("r", encoding="utf-8") as handle:
+                            data = json.load(handle)
+                else:
+                    with result_path.open("r", encoding="utf-8") as handle:
+                        data = json.load(handle)
+                return self.send_json({"available": True, "results": public_results(data)})
```

#### 4. Python 3.13 Multipart Parser Implementation
- **Why**: Pure standard-library replacement for `cgi.FieldStorage` using `email.parser.BytesParser`.
- **Diff**:
```diff
@@ -551,20 +560,53 @@ class RestaurantVisionHandler(BaseHTTPRequestHandler):
             raise ValueError("Invalid request size")
         return json.loads(self.rfile.read(length))
 
-    def multipart(self) -> cgi.FieldStorage:
+    def multipart(self) -> dict:
         content_type = self.headers.get("Content-Type", "")
         if not content_type.startswith("multipart/form-data"):
             raise ValueError("Expected a multipart upload")
-        return cgi.FieldStorage(
-            fp=self.rfile,
-            headers=self.headers,
-            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
-        )
+        length = int(self.headers.get("Content-Length", "0"))
+        if length <= 0 or length > 500_000_000:
+            raise ValueError("Invalid request size")
+        
+        body = self.rfile.read(length)
+        import email.parser, io
+        msg = email.parser.BytesParser().parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
+        
+        class Field:
+            def __init__(self, value=None, file=None, filename=None):
+                self.value = value
+                self.file = file
+                self.filename = filename
+
+        form = {}
+        for part in msg.walk():
+            if part.get_content_maintype() == 'multipart':
+                continue
+            disp = part.get("Content-Disposition", "")
+            if not disp.startswith("form-data"):
+                continue
+            
+            name = None
+            filename = None
+            for param in disp.split(";"):
+                param = param.strip()
+                if param.startswith("name="):
+                    name = param[5:].strip('"')
+                elif param.startswith("filename="):
+                    filename = param[9:].strip('"')
+            
+            if name:
+                payload = part.get_payload(decode=True)
+                if filename is not None:
+                    form[name] = Field(file=io.BytesIO(payload), filename=filename)
+                else:
+                    form[name] = Field(value=payload.decode('utf-8', 'ignore'))
+        return form
 
     @staticmethod
-    def field_text(form: cgi.FieldStorage, name: str, default: str = "") -> str:
-        field = form[name] if name in form else None
-        return str(field.value) if field is not None else default
+    def field_text(form: dict, name: str, default: str = "") -> str:
+        field = form.get(name)
+        return str(field.value) if field is not None and field.value is not None else default
```

#### 5. Concurrent Analysis Guard & Atomic Results Lock
- **Diff**:
```diff
@@ -679,6 +721,9 @@ class RestaurantVisionHandler(BaseHTTPRequestHandler):
         self.send_json(public_project(project))
 
     def start_analysis(self) -> None:
+        with JOBS_LOCK:
+            if any(job.get("status") in {"running", "queued"} for job in JOBS.values()):
+                raise ValueError('An analysis is already in progress')
         project = ProjectStore.load()
         job_id = uuid.uuid4().hex[:12]
         job = {"id": job_id, "status": "queued", "progress": 0.0, "message": "Preparing models"}
@@ -696,7 +741,8 @@ class RestaurantVisionHandler(BaseHTTPRequestHandler):
                 run_id = f"run-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{job_id[:5]}"
                 results["runId"] = run_id
                 results["corrections"] = []
-                save_json_atomic(RESULTS_PATH, results)
+                with RESULTS_LOCK:
+                    save_json_atomic(RESULTS_PATH, results)
                 save_json_atomic(RUNS_DIR / f"{run_id}.json", results)
                 task_count = TaskStore.generate_for_run(results, project)
                 with JOBS_LOCK:
@@ -713,10 +759,11 @@ class RestaurantVisionHandler(BaseHTTPRequestHandler):
         if not RESULTS_PATH.exists():
             raise ValueError("Run an analysis before making corrections")
         request = self.read_json()
-        with RESULTS_PATH.open("r", encoding="utf-8") as handle:
-            results = json.load(handle)
-        corrected = apply_result_correction(ProjectStore.load(), results, request)
-        save_json_atomic(RESULTS_PATH, corrected)
+        with RESULTS_LOCK:
+            with RESULTS_PATH.open("r", encoding="utf-8") as handle:
+                results = json.load(handle)
+            corrected = apply_result_correction(ProjectStore.load(), results, request)
+            save_json_atomic(RESULTS_PATH, corrected)
         if corrected.get("runId"):
             save_json_atomic(RUNS_DIR / f"{corrected['runId']}.json", corrected)
         self.send_json({"results": public_results(corrected), "correction": corrected.get("corrections", [])[-1]})
```

---

### B. `app/analytics.py`

#### 1. Identity Bank Sample Capping and Re-resolution
- **Why**: Avoids memory leaks from uncapped customer embedding arrays, and avoids locking an anonymous customer before real face evidence is gathered.
- **Diff**:
```diff
@@ -39,8 +39,9 @@ class IdentityBank:
                 if score >= self.staff_threshold:
                     return f"staff:{entry['id']}", "staff", score
 
-            if current_id and current_id.startswith("customer:"):
+            if current_id and current_id.startswith("customer:") and self.customers.get(current_id):
                 self.customers.setdefault(current_id, []).append(list(embedding))
+                self.customers[current_id] = self.customers[current_id][-20:]
                 return current_id, "customer", 1.0
 
             candidates = [
```

#### 2. Prevent Merging Two Enrolled Staff Identifiers
- **Why**: Prevents transitive Hungarian matches (`Staff A -> Customer X -> Staff B`) from collapsing distinct staff members into a single profile.
- **Diff**:
```diff
@@ -396,6 +397,8 @@ class VideoAnalyzer:
             left_root, right_root = find(left), find(right)
             if left_root == right_root:
                 return
+            if left_root.startswith("staff:") and right_root.startswith("staff:") and left_root != right_root:
+                return
             if left_root.startswith("staff:") != right_root.startswith("staff:"):
                 canonical = left_root if left_root.startswith("staff:") else right_root
             else:
```

#### 3. Table Party Dwell-Time Filter & Occlusion Gap Staff Counting
- **Why**: Transient pedestrians walking by tables were inflating party sizes to 10+ people. Customers also suffered brief occlusions which previously discarded waiter interaction time.
- **Diff**:
```diff
@@ -504,8 +507,13 @@ class VideoAnalyzer:
             party_sizes = []
             for session_index, session in enumerate(sessions, 1):
                 start_bucket, end_bucket = session[0], session[-1]
-                customer_ids = set().union(*(timeline[bucket]["customers"] for bucket in session))
-                staff_by_bucket = {bucket: timeline[bucket]["staff"] for bucket in session if timeline[bucket]["staff"]}
+                customer_counts = defaultdict(int)
+                for bucket in session:
+                    for cid in timeline[bucket]["customers"]:
+                        customer_counts[cid] += 1
+                min_buckets = max(1, round(15.0 / interval))
+                customer_ids = {cid for cid, count in customer_counts.items() if count >= min_buckets}
                 staff_by_bucket = {bucket: timeline[bucket]["staff"] for bucket in range(start_bucket, end_bucket + 1) if timeline[bucket]["staff"]}
                 staff_buckets = sorted(staff_by_bucket)
                 visits = self._contiguous_groups(staff_buckets, max_gap=max(1, round(2.0 / interval)))
                 total_visits += len(visits)
```

---

### C. `app/tracking.py`

#### 1. Clamp Predicted Bounding Box Dimensions
- **Why**: Velocity extrapolation over dropped frames previously produced negative width/height, causing invalid IoU numbers.
- **Diff**:
```diff
@@ -40,7 +40,10 @@ class Track:
 
     def predicted_bbox(self, timestamp: float) -> List[float]:
         elapsed = max(0.0, min(float(timestamp - self.last_seen), 3.0))
-        return [self.bbox[index] + self.velocity[index] * elapsed for index in range(4)]
+        predicted = [self.bbox[index] + self.velocity[index] * elapsed for index in range(4)]
+        predicted[2] = max(predicted[2], 1.0)
+        predicted[3] = max(predicted[3], 1.0)
+        return predicted
```

#### 2. Precompute and Cache Vector Averages in ByteTracker
- **Why**: Avoids re-computing expensive NumPy L2 vector normalizations `T x D` times during bipartite cost matrix creation.
- **Diff**:
```diff
@@ -170,11 +173,11 @@ class ByteTracker:
         timestamp: float,
         diagonal: float,
     ) -> float:
-        predicted = track.predicted_bbox(timestamp)
+        predicted = track._cached_predicted
         overlap = bbox_iou(predicted, detection.bbox)
         distance = center_distance(predicted, detection.bbox, diagonal)
-        face_score = cosine_similarity(track.embedding, detection.embedding)
-        body_score = cosine_similarity(track.appearance, detection.appearance)
+        face_score = cosine_similarity(track._cached_embedding, detection.embedding)
+        body_score = cosine_similarity(track._cached_appearance, detection.appearance)
         plausible = (
             overlap >= 0.01
             or distance <= 0.15
@@ -195,7 +198,7 @@ class ByteTracker:
         timestamp: float,
         diagonal: float,
     ) -> float:
-        predicted = track.predicted_bbox(timestamp)
+        predicted = track._cached_predicted
         overlap = bbox_iou(predicted, detection.bbox)
         distance = center_distance(predicted, detection.bbox, diagonal)
         if overlap < 0.02 and distance > 0.085:
@@ -246,6 +249,13 @@ class ByteTracker:
         low = [detection for detection in detections if self.low_score <= detection.score < self.high_score]
         current_tracks = list(self.tracks.values())
 
+        # Precompute per-track caches to avoid redundant recomputation during
+        # cost matrix construction.
+        for track in current_tracks:
+            track._cached_embedding = track.embedding
+            track._cached_appearance = track.appearance
+            track._cached_predicted = track.predicted_bbox(timestamp)
+
         primary_pairs, unmatched_track_indices, unmatched_high = self._associate(
             current_tracks,
             high,
@@ -293,6 +303,15 @@ class ByteTracker:
                 ended.append(self.tracks.pop(track_id))
 
         active.sort(key=lambda track: track.local_id)
+
+        # Clean up per-frame caches so stale data never leaks across frames.
+        for track in current_tracks:
+            for attr in ("_cached_embedding", "_cached_appearance", "_cached_predicted"):
+                try:
+                    delattr(track, attr)
+                except AttributeError:
+                    pass
+
         return active, ended
```

---

### D. `app/geometry.py`

#### 1. Coordinate Clamping on Homography Projections
- **Why**: Horizon or edge points projected through `cv2.perspectiveTransform` could produce astronomical numbers outside valid boundaries.
- **Diff**:
```diff
@@ -37,7 +37,10 @@ def project_point(point: Point, homography: Optional[np.ndarray]) -> Optional[Po
     destination = cv2.perspectiveTransform(source, homography)[0][0]
     if not np.isfinite(destination).all():
         return None
-    return float(destination[0]), float(destination[1])
+    x, y = float(destination[0]), float(destination[1])
+    if x < -0.5 or x > 1.5 or y < -0.5 or y > 1.5:
+        return None
+    return x, y
```

---

### E. `app/vision.py`

#### 1. Keep Highest-Confidence Face per Person Bounding Box
- **Why**: When people overlap or multiple faces appear within a bounding box, the last face in the loop was overwriting previous faces. Now it assigns the face with the highest confidence or largest face area.
- **Diff**:
```diff
@@ -158,8 +158,10 @@ class VisionModels:
                 appearance = self.body_embedding(crop)
             detections.append(Detection(bbox=box, score=score, appearance=appearance))
 
+        assigned_confidence: Dict[int, float] = {}
         for face, embedding in faces:
             fx, fy, fw, fh = [float(value) for value in face[:4]]
+            face_conf = float(face[14]) if len(face) > 14 else fw * fh
             center = (fx + fw / 2, fy + fh / 2)
             containing = []
             for index, detection in enumerate(detections):
@@ -168,7 +170,9 @@ class VisionModels:
                     containing.append((width * height, index))
             if containing:
                 _, index = min(containing)
-                detections[index].embedding = embedding
+                if index not in assigned_confidence or face_conf > assigned_confidence[index]:
+                    detections[index].embedding = embedding
+                    assigned_confidence[index] = face_conf
         return detections
```

---

### F. `static/app.js`

#### 1. Video Element Teardown and Cleanup in Review View
- **Why**: Prevents hardware video decoders and buffer memory leaks when switching cameras or re-rendering review grids.
- **Diff**:
```diff
@@ -521,6 +521,11 @@ function renderReview() {
   const grid = $("#reviewVideoGrid");
   const signature = cameras.map(camera => camera.id).join(",");
   if (grid.dataset.signature !== signature) {
+    grid.querySelectorAll('video').forEach(video => {
+      video.pause();
+      video.removeAttribute('src');
+      video.load();
+    });
     grid.dataset.signature = signature;
     grid.innerHTML = cameras.map(camera => `<div class="review-feed" data-camera-id="${camera.id}"><span class="feed-label">${escapeHTML(camera.name)}</span><video muted playsinline preload="auto" src="${mediaUrl(camera.videoPath)}?t=${Date.now()}" onloadeddata="this.currentTime=0.001"></video><canvas width="${camera.metadata?.width || 800}" height="${camera.metadata?.height || 450}"></canvas></div>`).join("") + `<div class="review-feed review-map-feed"><span class="feed-label">SHARED FLOOR PLAN</span><canvas id="reviewMapCanvas" width="1000" height="650"></canvas></div>`;
     $$("video", grid).forEach(video => video.addEventListener("error", () => toast("A camera video could not be loaded", true)));
```

#### 2. Polling Timeout and Progress Card Cleanup
- **Why**: Protects against infinite polling loops if an analysis thread dies, and ensures the "Analyze" button is always restored.
- **Diff**:
```diff
@@ -665,19 +670,33 @@ function renderAll() {
 async function pollJob(jobId) {
   $("#progressCard").hidden = false;
   $("#analyzeButton").disabled = true;
-  while (true) {
-    const job = await api(`/api/jobs/${jobId}`);
-    const progress = Math.round((job.progress || 0) * 100);
-    $("#progressMessage").textContent = job.message || "Analyzing";
-    $("#progressPercent").textContent = `${progress}%`;
-    $("#progressBar").style.width = `${progress}%`;
-    if (job.status === "complete") {
-      const response = await api("/api/results");
-      state.results = response.results;
-      renderReports(); renderReadiness(); toast(job.message || "Analysis complete"); showView("reports"); return;
+  const MAX_POLLS = 28800; // ~8 hours at 1-second intervals
+  let iterations = 0;
+  let completedSuccessfully = false;
+  try {
+    while (true) {
+      if (iterations++ >= MAX_POLLS) {
+        throw new Error("Analysis polling timed out after 8 hours");
+      }
+      const job = await api(`/api/jobs/${jobId}`);
+      const progress = Math.round((job.progress || 0) * 100);
+      $("#progressMessage").textContent = job.message || "Analyzing";
+      $("#progressPercent").textContent = `${progress}%`;
+      $("#progressBar").style.width = `${progress}%`;
+      if (job.status === "complete") {
+        const response = await api("/api/results");
+        state.results = response.results;
+        completedSuccessfully = true;
+        renderReports(); renderReadiness(); toast(job.message || "Analysis complete"); showView("reports"); return;
+      }
+      if (job.status === "error") { renderReadiness(); throw new Error(job.message || "Analysis failed"); }
+      await new Promise(resolve => setTimeout(resolve, 1000));
+    }
+  } finally {
+    $("#analyzeButton").removeAttribute("disabled");
+    if (!completedSuccessfully) {
+      setTimeout(() => { $("#progressCard").hidden = true; }, 5000);
     }
-    if (job.status === "error") { renderReadiness(); throw new Error(job.message || "Analysis failed"); }
-    await new Promise(resolve => setTimeout(resolve, 1000));
   }
 }
```

---

## 4. Untracked Temporary Files

During testing, the following file was generated in the root directory:
- `kernel.errors.txt`: An OpenVINO/Intel GPU driver dump from the hardware diagnostic test. This file is temporary and should not be committed to your repository.
