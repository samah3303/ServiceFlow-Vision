from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:
    from openvino import Core
except ImportError:  # The colour-histogram fallback keeps setup diagnostics usable.
    Core = None

try:
    import onnxruntime as ort
except ImportError:  # OpenCV remains the CPU fallback.
    ort = None

from .tracking import Detection

_FAILED_BODY_REID_DEVICES = set()


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

        self.requested_device = os.environ.get("SERVICEFLOW_INFERENCE_DEVICE", "auto").lower()
        if self.requested_device not in {"auto", "gpu", "cpu"}:
            raise ValueError("SERVICEFLOW_INFERENCE_DEVICE must be auto, gpu or cpu")
        self.person_net = None
        self.person_session = None
        self.person_compiled = None
        self.person_input_name = None
        self.person_backend = "OpenCV CPU"
        self.body_reid_backend = "HSV fallback"
        self.openvino_core = None
        if Core is not None:
            try:
                self.openvino_core = Core()
            except RuntimeError:
                pass
        self._configure_person_backend()
        self.face_detector = cv2.FaceDetectorYN.create(
            str(self.face_detector_path), "", (320, 320), 0.68, 0.3, 5000
        )
        self.face_recognizer = cv2.FaceRecognizerSF.create(str(self.face_recognizer_path), "")
        self.face_retry_every = 3
        self.face_retry_limit = 4
        self.body_reid_net = None
        self.body_reid_output = None
        if self.body_reid_xml_path.exists() and self.body_reid_bin_path.exists():
            try:
                if self.openvino_core is None:
                    raise RuntimeError("OpenVINO runtime is not installed")
                model = self.openvino_core.read_model(
                    model=str(self.body_reid_xml_path),
                    weights=str(self.body_reid_bin_path),
                )
                devices = self.openvino_core.available_devices
                preferred = next((device for device in devices if device.startswith("GPU")), None)
                if self.requested_device == "cpu":
                    preferred = None
                for device in ([preferred, "CPU"] if preferred else ["CPU"]):
                    failure_key = (str(self.body_reid_xml_path), device)
                    if failure_key in _FAILED_BODY_REID_DEVICES:
                        continue
                    try:
                        self.body_reid_net = self.openvino_core.compile_model(model, device)
                        self.body_reid_output = self.body_reid_net.output(0)
                        self.body_reid_backend = f"OpenVINO {device}"
                        break
                    except (RuntimeError, ValueError) as error:
                        _FAILED_BODY_REID_DEVICES.add(failure_key)
                        logging.warning("Body ReID unavailable on %s: %s", device, error)
            except (RuntimeError, ValueError):
                # The application remains inspectable with its colour-histogram
                # fallback if the optional runtime cannot initialize.
                self.body_reid_net = None
                self.body_reid_output = None
        self.input_size = 640
        self._grid: Optional[np.ndarray] = None
        self._expanded_strides: Optional[np.ndarray] = None

    def _configure_person_backend(self) -> None:
        if self.requested_device != "cpu" and ort is not None:
            providers = ort.get_available_providers()
            candidates = []
            if "CUDAExecutionProvider" in providers:
                candidates.append(("CUDAExecutionProvider", {}, "ONNX Runtime CUDA"))
            if "CoreMLExecutionProvider" in providers:
                candidates.append(("CoreMLExecutionProvider", {
                    "ModelFormat": "MLProgram",
                    "MLComputeUnits": "ALL",
                    "RequireStaticInputShapes": "1",
                    "EnableOnSubgraphs": "1",
                }, "ONNX Runtime Core ML"))
            for provider, options, label in candidates:
                try:
                    session_options = ort.SessionOptions()
                    session_options.log_severity_level = 3
                    session = ort.InferenceSession(
                        str(self.person_model_path), sess_options=session_options,
                        providers=[(provider, options), "CPUExecutionProvider"],
                    )
                    if provider not in session.get_providers():
                        continue
                    self.person_session = session
                    self.person_input_name = session.get_inputs()[0].name
                    self.person_backend = label
                    return
                except Exception as error:
                    logging.warning("Person detector unavailable on %s: %s", label, error)

        if self.requested_device != "cpu" and self.openvino_core is not None:
            for device in self.openvino_core.available_devices:
                if not device.startswith("GPU"):
                    continue
                try:
                    self.person_compiled = self.openvino_core.compile_model(str(self.person_model_path), device)
                    self.person_backend = f"OpenVINO {device}"
                    return
                except (RuntimeError, ValueError) as error:
                    logging.warning("Person detector unavailable on OpenVINO %s: %s", device, error)

        if self.requested_device == "gpu":
            logging.warning("No compatible GPU inference backend is available; using OpenCV CPU")
        self.person_net = cv2.dnn.readNet(str(self.person_model_path))

    def runtime_info(self) -> Dict[str, str]:
        return {
            "personDetector": self.person_backend,
            "faceDetector": "OpenCV YuNet CPU with person-crop retry",
            "faceRecognizer": "OpenCV SFace CPU",
            "bodyReidentification": self.body_reid_backend,
        }

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
        blob = np.ascontiguousarray(np.transpose(padded[:, :, ::-1].astype(np.float32), (2, 0, 1))[None, ...])
        if self.person_session is not None:
            output = np.asarray(self.person_session.run(None, {self.person_input_name: blob})[0])
        elif self.person_compiled is not None:
            output = np.asarray(next(iter(self.person_compiled([blob]).values())))
        else:
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

    def _face_candidates(self, frame: np.ndarray, max_dimension: Optional[int] = None) -> List[np.ndarray]:
        height, width = frame.shape[:2]
        scale = min(1.0, max_dimension / max(width, height)) if max_dimension else 1.0
        search = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1.0 else frame
        self.face_detector.setInputSize((search.shape[1], search.shape[0]))
        _, faces = self.face_detector.detect(search)
        if faces is None:
            return []
        if scale < 1.0:
            faces = faces.copy()
            faces[:, :14] /= scale
        return list(faces)

    def _face_embedding(self, frame: np.ndarray, face: np.ndarray) -> Optional[List[float]]:
        try:
            aligned = self.face_recognizer.alignCrop(frame, face)
            feature = self.face_recognizer.feature(aligned).reshape(-1).astype(np.float32)
            norm = float(np.linalg.norm(feature))
            if norm:
                feature /= norm
                return feature.astype(float).tolist()
        except cv2.error:
            pass
        return None

    def detect_faces(self, frame: np.ndarray) -> List[Tuple[np.ndarray, List[float]]]:
        results = []
        for face in self._face_candidates(frame):
            embedding = self._face_embedding(frame, face)
            if embedding is not None:
                results.append((face, embedding))
        return results

    def detections(self, frame: np.ndarray, frame_index: int = 0) -> List[Detection]:
        # ByteTrack's second association pass uses lower-confidence detections
        # to bridge partial occlusions; only high-confidence boxes start tracks.
        people = self.detect_people(frame, confidence=0.12)
        detections = []
        frame_height, frame_width = frame.shape[:2]
        for box, score in people:
            x, y, width, height = [int(value) for value in box]
            crop = frame[max(0, y):min(frame_height, y + height), max(0, x):min(frame_width, x + width)]
            appearance = None
            if crop.size:
                appearance = self.body_embedding(crop)
            detections.append(Detection(bbox=box, score=score, appearance=appearance))

        if not detections:
            return detections
        matched_faces: Dict[int, np.ndarray] = {}
        for face in self._face_candidates(frame, max_dimension=1280):
            fx, fy, fw, fh = [float(value) for value in face[:4]]
            center = (fx + fw / 2, fy + fh / 2)
            containing = []
            for index, detection in enumerate(detections):
                x, y, width, height = detection.bbox
                if x <= center[0] <= x + width and y <= center[1] <= y + 0.72 * height:
                    containing.append((width * height, index))
            if containing:
                _, index = min(containing)
                previous = matched_faces.get(index)
                if previous is None or float(face[14]) > float(previous[14]):
                    matched_faces[index] = face
        for index, face in matched_faces.items():
            detections[index].embedding = self._face_embedding(frame, face)

        if frame_index % self.face_retry_every == 0:
            candidates = sorted(
                (detection for detection in detections if detection.embedding is None and detection.score >= 0.35),
                key=lambda detection: detection.bbox[2] * detection.bbox[3], reverse=True,
            )[:self.face_retry_limit]
            for detection in candidates:
                x, y, width, height = [int(value) for value in detection.bbox]
                roi = frame[max(0, y):min(frame_height, y + round(height * 0.72)),
                            max(0, x):min(frame_width, x + width)]
                if roi.shape[0] < 40 or roi.shape[1] < 40:
                    continue
                scale = min(2.5, 640 / max(roi.shape[:2]))
                if scale > 1.1:
                    roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                roi_faces = self._face_candidates(roi)
                if roi_faces:
                    face = max(roi_faces, key=lambda item: float(item[2] * item[3] * item[14]))
                    detection.embedding = self._face_embedding(roi, face)
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
