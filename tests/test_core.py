import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analytics import IdentityBank, VideoAnalyzer
from app.evaluation import evaluate
import server
from server import TaskStore, apply_result_correction, calibration_quality, preserve_staff_embeddings, public_project, public_results
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
        self.assertIsNotNone(models.body_reid_net)
        crop = np.full((256, 128, 3), 127, dtype=np.uint8)
        embedding = models.body_embedding(crop)
        self.assertIsNotNone(embedding)
        self.assertGreater(len(embedding), 100)
        self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0, places=4)


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


if __name__ == "__main__":
    unittest.main()
