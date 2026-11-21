import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analytics import IdentityBank, VideoAnalyzer
from app.consistency import autocorrect_camera_maps, deduplicate_same_frame, enforce_exclusive_identity, enforce_same_frame_exclusivity
from app.presence import SeatedPresence
from app.spatial_sync import refine_spatial_time
from app.evaluation import evaluate
from app.timestamps import parse_timestamp, synchronize_project_cameras
import server
from server import RestaurantVisionHandler, TaskStore, apply_result_correction, calibration_quality, preserve_staff_embeddings, public_project, public_results
from app.geometry import cosine_similarity, homography_from_calibration, nearest_table, project_point
from app.tracking import ByteTracker, CentroidTracker, Detection, optimal_assignment
from app.vision import VisionModels


class GeometryTests(unittest.TestCase):
    def test_homography_projects_camera_to_shared_map(self):
        calibration = [
            {"image": [0, 0], "map": [0.1, 0.2]},
            {"image": [1, 0], "map": [0.9, 0.2]},
            {"image": [1, 1], "map": [0.9, 0.8]},
            {"image": [0, 1], "map": [0.1, 0.8]},
        ]
        matrix = homography_from_calibration(calibration, 1000, 500)
        point = project_point((500, 250), matrix)
        self.assertAlmostEqual(point[0], 0.5, places=3)
        self.assertAlmostEqual(point[1], 0.5, places=3)

    def test_table_proximity(self):
        table = {"id": "t1", "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
        self.assertEqual(nearest_table((0.62, 0.5), [table], 0.03)["id"], "t1")
        self.assertIsNone(nearest_table((0.8, 0.8), [table], 0.03))

    def test_seat_zone_assigns_its_linked_table(self):
        table = {"id": "t1", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}
        zone = {"id": "z1", "type": "seat", "tableId": "t1", "x": 0.7, "y": 0.7, "w": 0.15, "h": 0.15}
        self.assertEqual(nearest_table((0.75, 0.75), [table], 0.02, [zone])["id"], "t1")

    def test_calibration_quality_rewards_well_spread_points(self):
        camera = {"calibration": [
            {"image": [0.05, 0.05], "map": [0.1, 0.1]},
            {"image": [0.95, 0.05], "map": [0.9, 0.1]},
            {"image": [0.95, 0.95], "map": [0.9, 0.9]},
            {"image": [0.05, 0.95], "map": [0.1, 0.9]},
            {"image": [0.5, 0.2], "map": [0.5, 0.23]},
            {"image": [0.5, 0.8], "map": [0.5, 0.77]},
        ]}
        quality = calibration_quality(camera)
        self.assertTrue(quality["ready"])
        self.assertGreaterEqual(quality["score"], 80)


class TrackingTests(unittest.TestCase):
    def test_tracks_nearby_person_across_frames(self):
        tracker = CentroidTracker(max_missing=2)
        first, _ = tracker.update([Detection([10, 10, 40, 100], 0.9)], 0.0, (400, 600, 3))
        second, _ = tracker.update([Detection([14, 11, 40, 100], 0.9)], 0.5, (400, 600, 3))
        self.assertEqual(first[0].local_id, second[0].local_id)

    def test_appearance_can_hold_a_track_when_position_jumps(self):
        tracker = CentroidTracker(max_missing=2)
        first, _ = tracker.update([Detection([10, 10, 40, 100], 0.9, appearance=[1.0, 0.0])], 0.0, (400, 600, 3))
        second, _ = tracker.update([Detection([180, 10, 40, 100], 0.9, appearance=[1.0, 0.0])], 0.5, (400, 600, 3))
        self.assertEqual(first[0].local_id, second[0].local_id)

    def test_low_confidence_detection_recovers_existing_track(self):
        tracker = ByteTracker(max_missing=2)
        first, _ = tracker.update([Detection([10, 10, 40, 100], 0.9)], 0.0, (400, 600, 3))
        recovered, _ = tracker.update([Detection([13, 10, 40, 100], 0.2)], 0.5, (400, 600, 3))
        self.assertEqual(first[0].local_id, recovered[0].local_id)

    def test_low_confidence_detection_does_not_start_a_track(self):
        tracker = ByteTracker(max_missing=2)
        active, _ = tracker.update([Detection([10, 10, 40, 100], 0.2)], 0.0, (400, 600, 3))
        self.assertEqual(active, [])

    def test_global_assignment_beats_greedy_pairing(self):
        assignments = optimal_assignment([[0.10, 0.20], [0.11, 0.90]], maximum_cost=1.0)
        self.assertEqual(set(assignments), {(0, 1), (1, 0)})


class AnalyticsTests(unittest.TestCase):
    def test_same_person_box_cannot_count_as_staff_and_customer(self):
        rows = [
            {"cameraId": "a", "time": 1, "globalId": "customer:1", "role": "customer",
             "bbox": [.2, .2, .2, .4], "confidence": .8, "identityConfidence": 0},
            {"cameraId": "a", "time": 1, "globalId": "staff:actor", "role": "staff",
             "bbox": [.2, .2, .2, .4], "confidence": .8, "identityConfidence": .9},
        ]
        kept, issues = deduplicate_same_frame(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["globalId"], "staff:actor")
        self.assertEqual(len(issues), 1)

    def test_one_identity_cannot_occupy_distant_places_at_same_time(self):
        rows = [
            {"cameraId": "a", "localId": 1, "time": 1, "globalId": "staff:actor", "role": "staff",
             "bbox": [.1, .1, .1, .2], "map": [.1, .1], "confidence": .8, "identityConfidence": .9},
            {"cameraId": "b", "localId": 2, "time": 1.1, "globalId": "staff:actor", "role": "staff",
             "bbox": [.1, .1, .1, .2], "map": [.8, .8], "confidence": .7, "identityConfidence": .6},
        ]
        issues = enforce_exclusive_identity(rows)
        self.assertEqual(issues["count"], 1)
        self.assertEqual(rows[0]["role"], "staff")
        self.assertEqual(rows[1]["role"], "unresolved")
        self.assertNotEqual(rows[0]["globalId"], rows[1]["globalId"])

    def test_two_customer_boxes_on_one_frame_are_split_not_erased(self):
        rows = [
            {"cameraId": "a", "localId": 1, "time": 2, "globalId": "customer:001", "role": "customer",
             "bbox": [.1, .1, .1, .2], "map": [.1, .1], "confidence": .9, "identityConfidence": .9},
            {"cameraId": "a", "localId": 2, "time": 2, "globalId": "customer:001", "role": "customer",
             "bbox": [.7, .1, .1, .2], "map": [.7, .1], "confidence": .8, "identityConfidence": .8},
        ]
        enforce_exclusive_identity(rows)
        self.assertEqual({row["role"] for row in rows}, {"customer"})
        self.assertNotEqual(rows[0]["globalId"], rows[1]["globalId"])

    def test_three_boxes_never_share_an_identity_even_with_existing_review_flags(self):
        rows = [
            {"cameraId": "a", "time": 2, "globalId": "staff:actor", "role": "staff",
             "bbox": [x, .1, .1, .2], "confidence": .9 - x,
             "identityConfidence": .9 if x == .1 else 0,
             "needsReview": "earlier-warning" if x == .7 else None}
            for x in (.1, .4, .7)
        ]
        issues = enforce_exclusive_identity(rows)
        self.assertGreaterEqual(issues["count"], 2)
        self.assertEqual(len({row["globalId"] for row in rows}), 3)
        self.assertEqual(sum(row["role"] == "staff" for row in rows), 1)

    def test_saved_result_collision_id_stays_stable_across_frames(self):
        rows = []
        for timestamp in (0, .25, .5):
            rows.extend([
                {"cameraId": "a", "time": timestamp, "globalId": "customer:001", "role": "customer",
                 "bbox": [.1, .1, .1, .2], "confidence": .9, "identityConfidence": .9},
                {"cameraId": "a", "time": timestamp, "globalId": "customer:001", "role": "customer",
                 "bbox": [.7 + timestamp / 10, .1, .1, .2], "confidence": .6, "identityConfidence": 0},
            ])
        issues = enforce_same_frame_exclusivity(rows)
        self.assertEqual(issues["count"], 3)
        self.assertEqual(len({row["globalId"] for row in rows}), 2)
        for timestamp in (0, .25, .5):
            frame = [row for row in rows if row["time"] == timestamp]
            self.assertEqual(len({row["globalId"] for row in frame}), len(frame))

    def test_map_autocorrection_refuses_one_identity_even_with_many_pairs(self):
        project = {"cameras": [{"id": "a"}, {"id": "b"}], "tables": [], "zones": [], "settings": {}}
        rows = []
        for second in range(20):
            for camera_id, x in (("a", .2), ("b", .16)):
                rows.append({"cameraId": camera_id, "time": second, "globalId": "staff:one",
                             "role": "staff", "map": [x, .3], "identityConfidence": .9})
        result = autocorrect_camera_maps(rows, [], project)
        self.assertEqual(result["correctedCameras"], 0)
        self.assertEqual(rows[1]["map"], [.16, .3])

    def test_map_autocorrection_requires_two_independent_staff_tracks(self):
        project = {"cameras": [{"id": "a"}, {"id": "b"}], "tables": [], "zones": [], "settings": {}}
        rows = []
        for identity, origin in (("staff:one", (.2, .3)), ("staff:two", (.5, .6))):
            for second in (0, 6, 12, 18):
                point = [origin[0] + second / 1000, origin[1] + second / 2000]
                for camera_id, shift in (("a", (0, 0)), ("b", (-.04, .03))):
                    rows.append({"cameraId": camera_id, "time": second, "globalId": identity,
                                 "role": "staff", "map": [point[0] + shift[0], point[1] + shift[1]],
                                 "identityConfidence": .9, "tableId": None, "zoneIds": []})
        result = autocorrect_camera_maps(rows, [], project)
        self.assertEqual(result["correctedCameras"], 1)
        self.assertAlmostEqual(result["cameras"][1]["shift"][0], .04, places=3)
        self.assertAlmostEqual(rows[1]["map"][0], rows[0]["map"][0], places=4)

    def test_timestamp_parser_and_alignment(self):
        self.assertEqual(parse_timestamp(["2026-09-09", "07:58:28"]).second, 28)
        project = {"cameras": [{"id": "a", "videoPath": "a.mp4", "timeOffsetSeconds": 0},
                               {"id": "b", "videoPath": "b.mp4", "timeOffsetSeconds": 5}]}
        def reading(_root, camera, _ocr):
            return {"status": "verified", "startTime": "2026-09-09 07:58:28" if camera["id"] == "a" else "2026-09-09 07:58:34",
                    "samples": [1, 2], "spreadSeconds": 0}
        with patch("app.timestamps.RapidOCR", return_value=object()), patch("app.timestamps.read_camera_clock", side_effect=reading):
            report = synchronize_project_cameras(ROOT, project)
        self.assertTrue(report["applied"])
        self.assertEqual(project["cameras"][1]["timeOffsetSeconds"], 6)

    def test_manual_fine_shift_survives_ocr_resync(self):
        project = {"cameras": [{"id": "a", "videoPath": "a.mp4", "timeOffsetSeconds": 0},
                               {"id": "b", "videoPath": "b.mp4", "timeOffsetSeconds": 7.5,
                                "manualTimeAdjustmentSeconds": 1.5}]}
        def reading(_root, camera, _ocr):
            return {"status": "verified", "startTime": "2026-09-09 07:58:28" if camera["id"] == "a" else "2026-09-09 07:58:34",
                    "samples": [1, 2], "spreadSeconds": 0}
        with patch("app.timestamps.RapidOCR", return_value=object()), patch("app.timestamps.read_camera_clock", side_effect=reading):
            synchronize_project_cameras(ROOT, project)
        self.assertEqual(project["cameras"][1]["timeOffsetSeconds"], 7.5)

    def test_staff_actor_can_be_registered_without_photo(self):
        handler = RestaurantVisionHandler.__new__(RestaurantVisionHandler)
        handler.multipart = lambda: {"name": SimpleNamespace(value="Actor G"),
                                     "role": SimpleNamespace(value="waiter")}
        replies = []
        handler.send_json = lambda value, status=None: replies.append(value)
        project = {"staff": []}
        with patch("server.ProjectStore.load", return_value=project), patch("server.ProjectStore.save"):
            handler.add_staff()
        self.assertEqual(replies[0]["staff"]["name"], "Actor G")
        self.assertIsNone(project["staff"][0]["embedding"])
        self.assertIsNone(project["staff"][0]["photoPath"])

    def test_seated_customer_persists_without_motion_until_visual_departure(self):
        seated = SeatedPresence("a", confirmation_seconds=.5, vacancy_frames=3)
        occupied = np.zeros((100, 100, 3), dtype=np.uint8)
        occupied[20:60, 20:50] = (20, 80, 190)
        vacant = np.zeros_like(occupied)
        row = {"cameraId": "a", "globalId": "customer:one", "role": "customer",
               "time": 0, "bbox": [.2, .2, .3, .4], "map": [.4, .4],
               "tableId": "t1", "zoneIds": [], "confidence": .8}
        self.assertEqual(seated.update(occupied, [row], 0), [])
        self.assertEqual(seated.update(occupied, [row], .5), [])
        held = seated.update(occupied, [], 1)
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0]["estimated"])
        self.assertEqual(held[0]["globalId"], "customer:one")
        self.assertEqual(len(seated.update(vacant, [], 1.5)), 1)
        self.assertEqual(len(seated.update(vacant, [], 2)), 1)
        self.assertEqual(seated.update(vacant, [], 2.5), [])

    def test_spatial_sync_requires_two_moving_staff_and_corrects_clear_lag(self):
        project = {"cameras": [{"id": "a"}, {"id": "b"}]}
        rows = []
        for identity, phase in (("staff:one", 0), ("staff:two", 1.3)):
            for second in range(50):
                x = .4 + .11 * np.sin(second * .29 + phase)
                y = .45 + .10 * np.cos(second * .37 + phase)
                for camera_id, lag, dx, dy in (("a", 0, 0, 0), ("b", 2, .04, -.02)):
                    rows.append({"cameraId": camera_id, "globalId": identity, "role": "staff",
                                 "identityConfidence": .9, "time": second + lag,
                                 "map": [x + dx, y + dy]})
        result = refine_spatial_time(rows, [], project)
        self.assertEqual(result["correctedCameras"], 1)
        self.assertEqual(result["cameras"][1]["adjustmentSeconds"], -2)
        self.assertEqual(rows[1]["time"], 0)
        one_staff = [row for row in rows if row["globalId"] == "staff:one"]
        self.assertEqual(refine_spatial_time(one_staff, [], project)["correctedCameras"], 0)

    def test_manual_seated_customer_fills_detection_gap_from_first_frame(self):
        camera = {"id": "cam-a", "timeOffsetSeconds": 0}
        table = {"id": "t1", "number": "01", "x": .3, "y": .3, "w": .2, "h": .2}
        mark = {"id": "seated-1", "cameraId": "cam-a", "role": "customer", "staffId": None,
                "tableId": "t1", "bbox": [.35, .3, .1, .2], "startSeconds": 0, "endSeconds": 3}
        observations = []
        for timestamp in (0, 1, 2, 3, 4):
            frame = []
            VideoAnalyzer._apply_manual_marks(frame, [mark], camera, timestamp, 800, 450,
                                              None, [table], [], .075)
            observations.extend(frame)
        self.assertEqual([row["time"] for row in observations], [0, 1, 2, 3])
        self.assertTrue(all(row["tableId"] == "t1" for row in observations))
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {"tables": [table], "staff": [], "settings": {}}
        analyzer.sample_fps = 1
        results = analyzer._build_results(observations, [], [])
        self.assertEqual(results["sessions"][0]["start"], 0)
        self.assertEqual(results["tables"][0]["turns"], 1)
        self.assertEqual(results["summary"]["manualObservations"], 4)

    def test_manual_staff_mark_relabels_matching_detection_without_duplicate(self):
        frame = [{"cameraId": "cam-a", "localId": 1, "time": 1, "globalId": "customer:001",
                  "role": "customer", "bbox": [.4, .3, .1, .3], "map": [.5, .5],
                  "tableId": None, "confidence": .8, "identityConfidence": 0}]
        mark = {"id": "server-1", "role": "staff", "staffId": "actor-1", "tableId": "t1",
                "bbox": [.39, .29, .12, .32], "startSeconds": 0, "endSeconds": 2}
        VideoAnalyzer._apply_manual_marks(frame, [mark], {"id": "cam-a"}, 1, 800, 450,
                                          None, [{"id": "t1"}], [], .075)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame[0]["globalId"], "staff:actor-1")
        self.assertEqual(frame[0]["tableId"], "t1")

    def test_manual_mark_validation_rejects_unbounded_or_missing_staff(self):
        project = server.default_project()
        project["cameras"] = [{"id": "cam-a", "metadata": {"durationSeconds": 10}, "calibration": []}]
        project["manualMarks"] = [{"id": "mark-a", "cameraId": "cam-a", "role": "customer",
                                   "bbox": [.2, .2, .2, .2], "startSeconds": 0, "endSeconds": 20}]
        with self.assertRaises(ValueError):
            server.RestaurantVisionHandler.validate_project(project)
        project["manualMarks"][0].update(role="staff", staffId="missing", endSeconds=5)
        with self.assertRaises(ValueError):
            server.RestaurantVisionHandler.validate_project(project)

    def test_customer_track_mismatch_does_not_poison_identity_bank(self):
        bank = IdentityBank([], customer_threshold=0.43)
        identity, _, _ = bank.resolve([1.0, 0.0])
        same, _, score = bank.resolve([0.0, 1.0], current_id=identity)
        self.assertEqual(same, identity)
        self.assertLess(score, 0.43)
        self.assertEqual(bank.customers[identity], [[1.0, 0.0]])

    def test_contiguous_sessions(self):
        self.assertEqual(VideoAnalyzer._contiguous_groups([1, 2, 3, 8, 9], 2), [[1, 2, 3], [8, 9]])

    def test_layout_continuity_links_cross_camera_tracklets(self):
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {"settings": {"handoffDistance": 0.12, "handoffSeconds": 8}}
        analyzer.identity_bank = IdentityBank([])
        observations = [
            {"globalId": "customer:001", "localId": 1},
            {"globalId": "customer:002", "localId": 1},
        ]
        tracklets = [
            {"cameraId": "a", "globalId": "customer:001", "role": "customer", "firstSeen": 0, "lastSeen": 5, "startMap": [0.2, 0.2], "endMap": [0.5, 0.5], "embedding": None},
            {"cameraId": "b", "globalId": "customer:002", "role": "customer", "firstSeen": 6, "lastSeen": 10, "startMap": [0.53, 0.52], "endMap": [0.8, 0.8], "embedding": None},
        ]
        linked_observations, linked_tracklets, handoffs = analyzer._link_cross_camera(observations, tracklets)
        self.assertEqual(linked_observations[0]["globalId"], linked_observations[1]["globalId"])
        self.assertEqual(linked_tracklets[0]["globalId"], linked_tracklets[1]["globalId"])
        self.assertEqual(handoffs[0]["basis"], "layout continuity")

    def test_configured_camera_route_gates_handoffs(self):
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {
            "settings": {"handoffDistance": 0.12, "handoffSeconds": 8, "bodyReidThreshold": 0.58},
            "cameraLinks": [{
                "id": "route-a-b", "name": "Dining to patio", "fromCameraId": "a", "toCameraId": "b",
                "exitZoneId": "exit-a", "entryZoneId": "entry-b", "mode": "handoff",
                "minSeconds": 0, "maxSeconds": 8, "maxDistance": 0.12, "bidirectional": False,
            }],
        }
        analyzer.identity_bank = IdentityBank([])
        observations = [
            {"globalId": "customer:001", "localId": 1},
            {"globalId": "customer:002", "localId": 1},
        ]
        tracklets = [
            {"cameraId": "a", "globalId": "customer:001", "role": "customer", "firstSeen": 0, "lastSeen": 5,
             "startMap": [0.2, 0.2], "endMap": [0.5, 0.5], "endZoneIds": ["exit-a"], "appearance": [1.0, 0.0]},
            {"cameraId": "b", "globalId": "customer:002", "role": "customer", "firstSeen": 6, "lastSeen": 10,
             "startMap": [0.53, 0.52], "endMap": [0.8, 0.8], "startZoneIds": ["entry-b"], "appearance": [1.0, 0.0]},
        ]
        linked_observations, _, handoffs = analyzer._link_cross_camera(observations, tracklets)
        self.assertEqual(linked_observations[0]["globalId"], linked_observations[1]["globalId"])
        self.assertEqual(handoffs[0]["routeId"], "route-a-b")
        self.assertEqual(handoffs[0]["basis"], "body ReID + configured route zones")

    def test_cross_camera_match_never_merges_staff_with_customer(self):
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {"settings": {"handoffDistance": .12, "handoffSeconds": 8}}
        analyzer.identity_bank = IdentityBank([])
        observations = [{"globalId": "staff:one"}, {"globalId": "customer:001"}]
        tracklets = [
            {"cameraId": "a", "globalId": "staff:one", "role": "staff", "firstSeen": 0,
             "lastSeen": 5, "startMap": [.2, .2], "endMap": [.5, .5], "embedding": [1., 0.]},
            {"cameraId": "b", "globalId": "customer:001", "role": "customer", "firstSeen": 6,
             "lastSeen": 10, "startMap": [.53, .52], "endMap": [.8, .8], "embedding": [1., 0.]},
        ]
        linked, _, handoffs = analyzer._link_cross_camera(observations, tracklets)
        self.assertEqual([row["globalId"] for row in linked], ["staff:one", "customer:001"])
        self.assertEqual(handoffs, [])

    def test_one_way_camera_route_rejects_reverse_movement(self):
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {
            "settings": {"handoffDistance": 0.12, "handoffSeconds": 8, "bodyReidThreshold": 0.58},
            "cameraLinks": [{
                "id": "route-a-b", "fromCameraId": "a", "toCameraId": "b", "mode": "handoff",
                "minSeconds": 0, "maxSeconds": 8, "maxDistance": 0.12, "bidirectional": False,
            }],
        }
        analyzer.identity_bank = IdentityBank([])
        observations = [{"globalId": "customer:001"}, {"globalId": "customer:002"}]
        tracklets = [
            {"cameraId": "b", "globalId": "customer:001", "role": "customer", "firstSeen": 0, "lastSeen": 5,
             "startMap": [0.2, 0.2], "endMap": [0.5, 0.5], "embedding": [1.0, 0.0]},
            {"cameraId": "a", "globalId": "customer:002", "role": "customer", "firstSeen": 6, "lastSeen": 10,
             "startMap": [0.53, 0.52], "endMap": [0.8, 0.8], "embedding": [1.0, 0.0]},
        ]
        linked_observations, _, handoffs = analyzer._link_cross_camera(observations, tracklets)
        self.assertNotEqual(linked_observations[0]["globalId"], linked_observations[1]["globalId"])
        self.assertEqual(handoffs, [])

    def test_identity_merge_is_audited_and_rebuilds_summary(self):
        project = {
            "tables": [], "staff": [],
            "settings": {"sampleFps": 2},
        }
        previous = {
            "generatedAt": "2026-01-01T00:00:00Z", "runId": "run-test",
            "observations": [
                {"cameraId": "a", "time": 1, "globalId": "customer:001", "role": "customer", "bbox": [0,0,1,1], "map": [0.1,0.1], "tableId": None, "confidence": .9, "identityConfidence": 0},
                {"cameraId": "b", "time": 2, "globalId": "customer:002", "role": "customer", "bbox": [0,0,1,1], "map": [0.2,0.2], "tableId": None, "confidence": .9, "identityConfidence": 0},
            ],
            "tracklets": [], "handoffs": [], "corrections": [],
        }
        corrected = apply_result_correction(project, previous, {"action": "merge", "sourceId": "customer:002", "targetId": "customer:001"})
        self.assertEqual(corrected["summary"]["customers"], 1)
        self.assertEqual(corrected["corrections"][0]["action"], "merge")

    def test_merge_cannot_assign_one_identity_to_two_boxes_in_one_frame(self):
        project = {"tables": [], "staff": [], "settings": {"sampleFps": 2}}
        previous = {"runId": "run-test", "observations": [
            {"cameraId": "a", "time": 1, "globalId": "customer:001", "role": "customer",
             "bbox": [.1, .1, .1, .2], "map": [.1, .1], "tableId": None,
             "confidence": .9, "identityConfidence": .9},
            {"cameraId": "a", "time": 1, "globalId": "customer:002", "role": "customer",
             "bbox": [.7, .1, .1, .2], "map": [.7, .1], "tableId": None,
             "confidence": .6, "identityConfidence": 0},
        ], "tracklets": [], "handoffs": [], "corrections": []}
        corrected = apply_result_correction(project, previous, {
            "action": "merge", "sourceId": "customer:002", "targetId": "customer:001"})
        rows = corrected["observations"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["globalId"] for row in rows}), 2)
        self.assertEqual(corrected["identityConsistency"]["sameFrameIdentitiesSplit"], 1)

    def test_result_build_does_not_retain_feature_vectors(self):
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.project = {"tables": [], "staff": [], "settings": {}}
        analyzer.sample_fps = 2
        results = analyzer._build_results([], [{"globalId": "customer:001", "embedding": [1.0], "appearance": [1.0]}], [])
        self.assertNotIn("embedding", results["tracklets"][0])
        self.assertNotIn("appearance", results["tracklets"][0])


class PrivacyTests(unittest.TestCase):
    def test_staff_embedding_stays_server_side(self):
        stored = {"staff": [{"id": "s1", "name": "Actor A", "embedding": [0.1, 0.2]}]}
        incoming = {"staff": [{"id": "s1", "name": "Actor A"}]}
        preserved = preserve_staff_embeddings(incoming, stored)
        self.assertEqual(preserved["staff"][0]["embedding"], [0.1, 0.2])
        self.assertNotIn("embedding", public_project(preserved)["staff"][0])

    def test_result_api_redacts_legacy_vectors(self):
        visible = public_results({"tracklets": [{"embedding": [1], "appearance": [2], "globalId": "customer:001"}]})
        self.assertEqual(visible["tracklets"], [{"globalId": "customer:001"}])


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_path = server.TASKS_PATH
        server.TASKS_PATH = Path(self.temporary.name) / "tasks.json"

    def tearDown(self):
        server.TASKS_PATH = self.previous_path
        self.temporary.cleanup()

    def test_analysis_creates_wedge_actions(self):
        project = {"managerSettings": {"greetingTargetSeconds": 90}}
        results = {"runId": "run-test", "summary": {"averageDetectionConfidence": .88}, "sessions": [{"tableId": "t1", "tableNumber": "01", "start": 0, "end": 180, "firstServiceSeconds": None, "staffVisits": 0}]}
        count = TaskStore.generate_for_run(results, project)
        tasks = TaskStore.load()["tasks"]
        self.assertEqual(count, 3)
        self.assertEqual({task["kind"] for task in tasks}, {"acknowledgement", "service_request", "reset"})

    def test_task_actions_record_outcomes(self):
        project = {"tables": [], "zones": [], "staff": [{"id": "s1", "name": "Actor A", "role": "waiter"}]}
        task = TaskStore.create_manual({"title": "Check Table 2", "requestedAction": "Visit the table", "targetRole": "waiter", "createdBy": "s1"}, project)
        accepted = TaskStore.act({"taskId": task["id"], "action": "accept", "actorId": "s1"}, project)
        completed = TaskStore.act({"taskId": task["id"], "action": "complete", "actorId": "s1"}, project)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual([item["action"] for item in completed["history"]][-2:], ["accept", "complete"])

    def test_task_cannot_be_claimed_by_wrong_role(self):
        project = {"tables": [], "zones": [], "staff": [{"id": "s1", "name": "Runner A", "role": "runner"}]}
        task = TaskStore.create_manual({"title": "Check table", "requestedAction": "Visit now", "targetRole": "waiter"}, project)
        with self.assertRaisesRegex(ValueError, "different role"):
            TaskStore.act({"taskId": task["id"], "action": "accept", "actorId": "s1"}, project)


class ModelSmokeTests(unittest.TestCase):
    def test_models_load_and_produce_embedding(self):
        models = VisionModels(ROOT / "models")
        self.assertTrue(models.person_model_path.exists())
        blank = np.zeros((240, 320, 3), dtype=np.uint8)
        self.assertEqual(models.detect_people(blank), [])

    def test_body_reidentification_model_produces_normalized_embedding(self):
        models = VisionModels(ROOT / "models")
        crop = np.full((256, 128, 3), 127, dtype=np.uint8)
        embedding = models.body_embedding(crop)
        self.assertIsNotNone(embedding)
        self.assertGreaterEqual(len(embedding), 96)
        self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0, places=4)
        self.assertIn("personDetector", models.runtime_info())

    def test_cpu_device_override_keeps_person_detector_available(self):
        with patch.dict(os.environ, {"SERVICEFLOW_INFERENCE_DEVICE": "cpu"}):
            models = VisionModels(ROOT / "models")
        self.assertEqual(models.person_backend, "OpenCV CPU")
        self.assertEqual(models.detect_people(np.zeros((240, 320, 3), dtype=np.uint8)), [])

    def test_person_crop_retry_can_recover_face_missed_in_full_frame(self):
        models = VisionModels(ROOT / "models")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        face = np.zeros(15, dtype=np.float32)
        face[2:4] = [40, 40]
        face[14] = 0.9
        with patch.object(models, "detect_people", return_value=[([100, 50, 160, 320], 0.95)]), \
             patch.object(models, "body_embedding", return_value=None), \
             patch.object(models, "_face_candidates", side_effect=[[], [face]]), \
             patch.object(models, "_face_embedding", return_value=[1.0, 0.0]):
            detections = models.detections(frame, frame_index=0)
        self.assertEqual(detections[0].embedding, [1.0, 0.0])


class EvaluationTests(unittest.TestCase):
    def test_perfect_predictions_score_one(self):
        prediction = {
            "observations": [
                {"cameraId": "a", "time": 1, "globalId": "customer:001", "bbox": [0, 0, 10, 10]},
                {"cameraId": "b", "time": 3, "globalId": "customer:001", "bbox": [1, 1, 10, 10]},
            ],
            "sessions": [{"tableId": "t1", "start": 0, "end": 10, "firstServiceSeconds": 4}],
            "handoffs": [{"fromCameraId": "a", "toCameraId": "b", "globalId": "customer:001", "time": 2}],
        }
        truth = {
            "observations": [
                {"cameraId": "a", "time": 1, "globalId": "guest-a", "bbox": [0, 0, 10, 10]},
                {"cameraId": "b", "time": 3, "globalId": "guest-a", "bbox": [1, 1, 10, 10]},
            ],
            "sessions": [{"tableId": "t1", "start": 0, "end": 10, "firstServiceSeconds": 4}],
            "handoffs": [{"fromCameraId": "a", "toCameraId": "b", "globalId": "guest-a", "time": 2}],
        }
        report = evaluate(prediction, truth)
        self.assertEqual(report["identity"]["idf1"], 1.0)
        self.assertEqual(report["tableOccupancy"]["f1"], 1.0)
        self.assertEqual(report["cameraHandoffs"]["f1"], 1.0)
        self.assertEqual(report["firstService"]["meanAbsoluteErrorSeconds"], 0.0)

    def test_observation_matching_uses_global_assignment_in_crowded_frames(self):
        from app.evaluation import match_observations

        predictions = [
            {"cameraId": "a", "time": 1, "bbox": [0, 0, 10, 10]},
            {"cameraId": "a", "time": 1, "bbox": [-2, 0, 10, 10]},
        ]
        truth = [
            {"cameraId": "a", "time": 1, "bbox": [0, 0, 10, 10]},
            {"cameraId": "a", "time": 1, "bbox": [3, 0, 10, 10]},
        ]

        # The first prediction perfectly matches truth[0], but it can also
        # match truth[1]. The second can only match truth[0]; global assignment
        # preserves both matches, where greedy matching would lose one.
        pairs = match_observations(predictions, truth)
        self.assertEqual(set(pairs), {(0, 1), (1, 0)})


if __name__ == "__main__":
    unittest.main()
