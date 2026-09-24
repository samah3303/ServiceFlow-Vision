# ServiceFlow Vision

ServiceFlow Vision is a private, local prototype that turns staged restaurant CCTV footage into coordinated action. It combines multiple camera angles on one manually calibrated floor plan, detects people, recognizes enrolled staff actors, creates temporary customer identities, reports observable service interactions, and routes recoverable service moments to the right shift role.

It contains three connected experiences:

- **Vision Lab:** setup, calibration, analysis, synchronized camera review and identity correction.
- **Shift Copilot:** a mobile-friendly queue where a staff actor accepts, completes, hands off or dismisses one clear action at a time.
- **Manager:** an operational view of shift health, table response, active coordination, five key replay moments and observable staff activity.

## Start the app

On macOS, double-click **Start Restaurant Vision.command**. The first launch creates a private Python environment and installs the required local-vision packages. Your browser then opens the app at `http://127.0.0.1:8765`.

Or start it from Terminal:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --open
```

All footage, staff photos, embeddings, configuration and results remain in this folder. The server listens only on the local computer by default.

## Recommended setup order

1. Add a floor-plan image or use the blank grid.
2. Draw around every table and its chairs, then confirm the table numbers.
3. Add seat zones when chairs extend beyond a table box, and add handoff zones where people move between camera views. Entrance, waiting and staff-only zones make the shared map easier to interpret.
4. Upload a synchronized recording for each camera. If recordings start at different times, enter the start-time offset in seconds.
5. Calibrate each camera by matching at least four visible floor points to the same points on the shared plan. Spread the points across the visible floor; six to eight are better. The app grades each calibration and flags weak views.
6. Place each camera icon on the plan. This is descriptive; point calibration performs the actual projection.
7. In **Camera topology**, add every physically possible camera-to-camera movement. Mark a route as a handoff for consecutive views or overlap when both cameras can see the person at once. Add exit/entry zones and realistic travel windows where possible.
8. Enroll each consented staff actor with a clear frontal photo and shift role.
9. Run the analysis and review the report.
10. Open **Review** to validate detections and correct identities. Corrections recalculate the operational report and remain in an audit history. Export training labels only after this review.
11. Open **Shift Copilot** to test the routed action queue as different staff roles. Actions can be accepted, completed, handed off or dismissed with feedback.
12. Open **Manager** for the owner/manager view. Use **Targets** to set first-service, long-session, visit and anonymous-data retention thresholds.

## Coordinated-action loop

The product follows: **see → understand → decide → assign → verify → learn**. The current wedge generates three evidence-linked action types:

- Acknowledge an occupied table that has missed the first-service target.
- Clear and reset a table after an observed occupied session ends.
- Resolve a service-coverage gap when a session has no detected staff visit.

Staff can also raise a concrete manual task and route it to a waiter, runner, busser, host, captain or manager. Each action keeps a local history of acceptance, completion, handoff or dismissal so routing quality can improve without becoming an employee ranking system.

## How cross-camera tracking works

Each person box is represented by the bottom-center point near their feet. Camera calibration projects that point onto the shared floor plan. Within a camera, a ByteTrack-style two-stage association first matches reliable detections and then uses lower-confidence boxes to recover people through short occlusions. A motion estimate, box overlap, face evidence and whole-body appearance all contribute without forcing an implausible match.

Across cameras, a 256-dimensional whole-body descriptor from OpenVINO's `person-reidentification-retail-0288` model is combined with face similarity and calibrated floor-plan motion. Only camera pairs explicitly allowed by **Camera topology** are considered once routes exist. The matcher enforces direction, exit/entry zones, travel windows and maximum map distance, then solves the candidate handoffs together with a global one-to-one assignment. Overlapping camera views compare synchronized path samples instead of pretending that one view must end before the next begins. Low-evidence layout-only links are marked for human review.

Customer profiles are created automatically and use labels such as `customer:004`. They are not matched against an external identity database. Staff are matched only against the profiles enrolled in this project.

Customer face and body feature vectors are used only while an analysis is running and are not retained in saved results. Staff embeddings remain in the local project because they are needed for recognition, but they are not sent to the browser or placed in exports. The manager can define a retention period and explicitly remove expired anonymous guest journeys and their derived analysis tasks.

## What the report means

- **Table session:** a continuous period in which at least one customer was detected near a table.
- **First service:** the first detected enrolled staff presence near an occupied table.
- **Staff visit:** a continuous staff presence near an occupied table, separated from another visit by at least two seconds.
- **Interaction time:** time physically detected near an occupied table. It does not claim to measure conversation quality, sentiment or intent.

The JSON exports include observations, tracklets, cross-camera handoffs, paths and calculated summaries so every metric can be audited. They omit biometric vectors. Each handoff includes its configured route, evidence, body/face similarity where available, overall match confidence and whether review is recommended.

## Validation workstation

The Review page plays uploaded cameras on one synchronized timeline. It draws person boxes and temporary identities on each video while showing the same people on the floor plan. A reviewer can:

- Merge two IDs that belong to the same person.
- Relabel an identity as an enrolled staff member or customer.
- Split a track at the current time when two people were incorrectly joined.
- Inspect the evidence used for cross-camera handoffs.
- Review the local correction history.
- Export a reviewed training-label manifest containing camera references, layout zones, boxes, tracks, identities and correction history.

## Manager dashboard

The Manager page contains guest count, table turns, average first service, service coverage, staff visits, open actions, a coordination queue, five-moment shift replay, table-and-zone activity map, occupied-table timeline, exceptions requiring review, calibration warnings, staff presence and table-level detail. It intentionally reports observable activity rather than an opaque employee score, and should support reviewed coaching and process improvement rather than automated employment decisions.

## Measure accuracy before using the metrics

The repository includes an offline evaluator for a predicted result and a human-reviewed ground-truth manifest:

```bash
.venv/bin/python -m app.evaluation prediction.json ground-truth.json --output evaluation.json
```

Both inputs use the app's result structure: `observations` contain camera, time, box and identity; `sessions` contain table, start, end and first-service time; `handoffs` contain source camera, target camera and time. The report measures detection precision/recall/F1, identity IDF1, table-occupancy F1 by second, camera-handoff F1 and first-service timing error. Validate on held-out staged shifts with different actors, clothing, lighting, crowding and camera routes. Tune thresholds on a validation split and publish results only on a separate test split.

## Prototype limitations

- Camera clocks must be synchronized or corrected with timeline offsets.
- Accurate mapping needs visible floor points and a reasonably flat floor plane.
- Overhead angles, heavy occlusion and very small faces reduce identity accuracy.
- Layout-only handoffs are hypotheses and should be reviewed when two people cross at the same camera boundary. Explicit camera routes narrow the search space but do not prove identity.
- The included models provide a baseline. Thresholds must be calibrated against your staged footage before interpreting staff metrics.
- This version processes uploaded recordings. RTSP/live-camera ingestion, pose/activity recognition and POS/KDS integration are logical next additions.
- Action generation is a replay/simulation layer for uploaded footage. Real-time alerts require live-stream ingestion and device notification delivery.
- Ordering, dining, payment and clearing states should not be inferred until supporting POS/KDS signals or validated vision events are connected.

## Included models and licences

The app includes three ONNX model files from the official OpenCV Zoo:

- YuNet face detection
- SFace face embeddings
- YOLOX-S object detection

It also includes the FP16 OpenVINO IR for `person-reidentification-retail-0288`, downloaded from Intel's official Open Model Zoo model storage. The cross-camera tracker is an independent compact implementation inspired by the ByteTrack paper and public MIT-licensed reference repository; no Ultralytics, BoxMOT or InsightFace packages or weights are bundled.

See `THIRD_PARTY_NOTICES.md` for exact source links, checksums and licence references. Preserve the upstream licence notices and re-check the terms before redistribution or commercial deployment.
