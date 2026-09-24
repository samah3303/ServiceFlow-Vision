from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:
    from openvino import Core
except ImportError:  # The colour-histogram fallback keeps setup diagnostics usable.
    Core = None

from .tracking import Detection


class VisionModels:
    """Local OpenCV models for people, faces, and whole-body embeddings."""

    def __init__(self, model_dir: Path) -> None:
        model_dir = Path(model_dir)
        self.person_model_path = model_dir / "object_detection_yolox_2022nov.onnx"
        self.face_detector_path = model_dir / "face_detection_yunet_2023mar.onnx"
        self.face_recognizer_path = model_dir / "face_recognition_sface_2021dec.onnx"
        self.body_reid_xml_path = model_dir / "person-reidentification-retail-0288.xml"
        self.body_reid_bin_path = model_dir / "person-reidentification-retail-0288.bin"
        missing = [
            str(path.name)
            for path in (self.person_model_path, self.face_detector_path, self.face_recognizer_path)
            if not path.exists()
        ]
        if missing:
            raise FileNotFoundError("Missing model files: " + ", ".join(missing))

        self.person_net = cv2.dnn.readNet(str(self.person_model_path))
        self.face_detector = cv2.FaceDetectorYN.create(
            str(self.face_detector_path), "", (320, 320), 0.82, 0.3, 5000
        )
        self.face_recognizer = cv2.FaceRecognizerSF.create(str(self.face_recognizer_path), "")
        self.body_reid_net = None
        self.body_reid_output = None
        if self.body_reid_xml_path.exists() and self.body_reid_bin_path.exists():
            try:
                if Core is None:
                    raise RuntimeError("OpenVINO runtime is not installed")
                core = Core()
                model = core.read_model(
                    model=str(self.body_reid_xml_path),
                    weights=str(self.body_reid_bin_path),
                )
                self.body_reid_net = core.compile_model(model, "CPU")
                self.body_reid_output = self.body_reid_net.output(0)
            except (RuntimeError, ValueError):
                # The application remains inspectable with its colour-histogram
                # fallback if the optional runtime cannot initialize.
                self.body_reid_net = None
                self.body_reid_output = None
        self.input_size = 640
        self._grid: Optional[np.ndarray] = None
        self._expanded_strides: Optional[np.ndarray] = None

    def _anchors(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._grid is not None and self._expanded_strides is not None:
            return self._grid, self._expanded_strides
        grids = []
        strides = []
        for stride in (8, 16, 32):
            height = width = self.input_size // stride
            xv, yv = np.meshgrid(np.arange(width), np.arange(height))
            grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((*grid.shape[:2], 1), stride))
        self._grid = np.concatenate(grids, axis=1).astype(np.float32)
        self._expanded_strides = np.concatenate(strides, axis=1).astype(np.float32)
        return self._grid, self._expanded_strides

    def detect_people(
        self,
        frame: np.ndarray,
        confidence: float = 0.35,
        nms_threshold: float = 0.45,
    ) -> List[Tuple[List[float], float]]:
        height, width = frame.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized = cv2.resize(frame, (int(width * scale), int(height * scale)))
        padded = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        # This OpenCV Zoo export expects unscaled RGB float values in NCHW order.
        blob = np.transpose(padded[:, :, ::-1].astype(np.float32), (2, 0, 1))[None, ...]
        self.person_net.setInput(blob)
        output = np.asarray(self.person_net.forward())
        if output.ndim == 2:
            output = output[None, ...]
        if output.shape[-1] != 85:
            output = output.reshape(1, -1, 85)

        grid, strides = self._anchors()
        predictions = output.astype(np.float32).copy()
        predictions[..., :2] = (predictions[..., :2] + grid) * strides
        predictions[..., 2:4] = np.exp(np.clip(predictions[..., 2:4], -10, 10)) * strides

        person_scores = predictions[0, :, 4] * predictions[0, :, 5]
        valid = person_scores >= confidence
        rows = predictions[0, valid]
        scores = person_scores[valid]
        if not len(rows):
            return []

        boxes: List[List[int]] = []
        clean_scores: List[float] = []
        for row, score in zip(rows, scores):
            cx, cy, box_width, box_height = row[:4]
            x = max(0.0, (cx - box_width / 2) / scale)
            y = max(0.0, (cy - box_height / 2) / scale)
            box_width = min(float(box_width / scale), width - x)
            box_height = min(float(box_height / scale), height - y)
            if box_width >= 8 and box_height >= 16:
                boxes.append([int(x), int(y), int(box_width), int(box_height)])
                clean_scores.append(float(score))

        if not boxes:
            return []
        indices = cv2.dnn.NMSBoxes(boxes, clean_scores, confidence, nms_threshold)
        kept = np.asarray(indices).reshape(-1).tolist() if len(indices) else []
        return [([float(value) for value in boxes[index]], clean_scores[index]) for index in kept]

    def detect_faces(self, frame: np.ndarray) -> List[Tuple[np.ndarray, List[float]]]:
        height, width = frame.shape[:2]
        self.face_detector.setInputSize((width, height))
        _, faces = self.face_detector.detect(frame)
        if faces is None:
            return []
        results = []
        for face in faces:
            try:
                aligned = self.face_recognizer.alignCrop(frame, face)
                feature = self.face_recognizer.feature(aligned).reshape(-1).astype(np.float32)
                norm = float(np.linalg.norm(feature))
                if norm:
                    feature /= norm
                results.append((face, feature.astype(float).tolist()))
            except cv2.error:
                continue
        return results

    def detections(self, frame: np.ndarray) -> List[Detection]:
        # ByteTrack's second association pass uses lower-confidence detections
        # to bridge partial occlusions; only high-confidence boxes start tracks.
        people = self.detect_people(frame, confidence=0.12)
        faces = self.detect_faces(frame)
        detections = []
        frame_height, frame_width = frame.shape[:2]
        for box, score in people:
            x, y, width, height = [int(value) for value in box]
            crop = frame[max(0, y):min(frame_height, y + height), max(0, x):min(frame_width, x + width)]
            appearance = None
            if crop.size:
                appearance = self.body_embedding(crop)
            detections.append(Detection(bbox=box, score=score, appearance=appearance))

        for face, embedding in faces:
            fx, fy, fw, fh = [float(value) for value in face[:4]]
            center = (fx + fw / 2, fy + fh / 2)
            containing = []
            for index, detection in enumerate(detections):
                x, y, width, height = detection.bbox
                if x <= center[0] <= x + width and y <= center[1] <= y + height:
                    containing.append((width * height, index))
            if containing:
                _, index = min(containing)
                detections[index].embedding = embedding
        return detections

    def body_embedding(self, person_crop: np.ndarray) -> Optional[List[float]]:
        """Return a normalized whole-body descriptor with a safe fallback."""

        if person_crop.size == 0:
            return None
        if self.body_reid_net is not None:
            try:
                blob = cv2.dnn.blobFromImage(
                    person_crop,
                    scalefactor=1.0,
                    size=(128, 256),
                    mean=(0, 0, 0),
                    swapRB=False,
                    crop=False,
                )
                feature = self.body_reid_net([blob])[self.body_reid_output].reshape(-1).astype(np.float32)
                norm = float(np.linalg.norm(feature))
                if norm:
                    feature /= norm
                    return feature.astype(float).tolist()
            except (RuntimeError, ValueError):
                pass

        # HSV remains useful as weak appearance evidence on machines where the
        # optional body model cannot load. It is never persisted in reports.
        hsv = cv2.cvtColor(person_crop, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist(
            [hsv], [0, 1], None, [12, 8], [0, 180, 0, 256]
        ).reshape(-1)
        norm = float(np.linalg.norm(histogram))
        if not norm:
            return None
        histogram /= norm
        return histogram.astype(float).tolist()

    def embedding_from_image(self, image: np.ndarray) -> List[float]:
        faces = self.detect_faces(image)
        if not faces:
            raise ValueError("No clear face was found in this image")
        face, embedding = max(faces, key=lambda item: float(item[0][2] * item[0][3]))
        return embedding
