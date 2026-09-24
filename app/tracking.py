from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import average_embeddings, bbox_iou, center_distance, cosine_similarity


@dataclass
class Detection:
    bbox: List[float]
    score: float
    embedding: Optional[List[float]] = None
    appearance: Optional[List[float]] = None


@dataclass
class Track:
    local_id: int
    bbox: List[float]
    first_seen: float
    last_seen: float
    score: float
    missing: int = 0
    role: str = "unknown"
    global_id: Optional[str] = None
    embeddings: List[List[float]] = field(default_factory=list)
    appearances: List[List[float]] = field(default_factory=list)
    positions: List[Tuple[float, float, float]] = field(default_factory=list)
    velocity: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    hits: int = 1

    @property
    def embedding(self) -> Optional[List[float]]:
        return average_embeddings(self.embeddings[-12:])

    @property
    def appearance(self) -> Optional[List[float]]:
        return average_embeddings(self.appearances[-20:])

    def predicted_bbox(self, timestamp: float) -> List[float]:
        elapsed = max(0.0, min(float(timestamp - self.last_seen), 3.0))
        return [self.bbox[index] + self.velocity[index] * elapsed for index in range(4)]

    def observe(self, detection: Detection, timestamp: float) -> None:
        elapsed = max(float(timestamp - self.last_seen), 1e-3)
        measured_velocity = [
            (detection.bbox[index] - self.bbox[index]) / elapsed
            for index in range(4)
        ]
        # An alpha-beta style update is steadier than raw frame-to-frame velocity
        # at the deliberately low sample rates used for CCTV review.
        self.velocity = [
            0.65 * previous + 0.35 * measured
            for previous, measured in zip(self.velocity, measured_velocity)
        ]
        self.bbox = list(detection.bbox)
        self.score = float(detection.score)
        self.last_seen = float(timestamp)
        self.missing = 0
        self.hits += 1
        if detection.embedding is not None:
            self.embeddings.append(detection.embedding)
            self.embeddings = self.embeddings[-24:]
        if detection.appearance is not None:
            self.appearances.append(detection.appearance)
            self.appearances = self.appearances[-32:]


def optimal_assignment(costs: Sequence[Sequence[float]], maximum_cost: float) -> List[Tuple[int, int]]:
    """Return a minimum-cost one-to-one assignment without a SciPy dependency.

    This is the rectangular Hungarian algorithm. Edges above ``maximum_cost``
    remain unassigned, which lets the tracker use explicit motion/appearance
    gates rather than forcing every person to match something.
    """

    if not costs or not costs[0]:
        return []
    matrix = [list(map(float, row)) for row in costs]
    row_count, column_count = len(matrix), len(matrix[0])
    transposed = row_count > column_count
    if transposed:
        matrix = [list(row) for row in zip(*matrix)]
        row_count, column_count = column_count, row_count

    u = [0.0] * (row_count + 1)
    v = [0.0] * (column_count + 1)
    p = [0] * (column_count + 1)
    way = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        p[0] = row
        minimum = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            active_row = p[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, column_count + 1):
                if used[candidate]:
                    continue
                reduced = matrix[active_row - 1][candidate - 1] - u[active_row] - v[candidate]
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    way[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(column_count + 1):
                if used[candidate]:
                    u[p[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if p[column] == 0:
                break
        while True:
            previous = way[column]
            p[column] = p[previous]
            column = previous
            if column == 0:
                break

    pairs: List[Tuple[int, int]] = []
    for column in range(1, column_count + 1):
        if p[column] == 0:
            continue
        row_index, column_index = p[column] - 1, column - 1
        original_row, original_column = (
            (column_index, row_index) if transposed else (row_index, column_index)
        )
        if float(costs[original_row][original_column]) <= maximum_cost:
            pairs.append((original_row, original_column))
    return pairs


class ByteTracker:
    """A compact ByteTrack-style tracker for sampled, fixed-camera footage.

    High-confidence detections are associated first using motion, overlap and
    optional appearance evidence. Lower-confidence detections get a second pass
    to recover occluded people, but can never create a new identity.
    """

    def __init__(
        self,
        max_missing: int = 10,
        high_score: float = 0.45,
        low_score: float = 0.12,
        new_track_score: float = 0.38,
    ) -> None:
        self.max_missing = max_missing
        self.high_score = high_score
        self.low_score = low_score
        self.new_track_score = new_track_score
        self.next_id = 1
        self.tracks: Dict[int, Track] = {}

    @staticmethod
    def _diagonal(frame_shape: Sequence[int]) -> float:
        return max((frame_shape[0] ** 2 + frame_shape[1] ** 2) ** 0.5, 1.0)

    def _primary_cost(
        self,
        track: Track,
        detection: Detection,
        timestamp: float,
        diagonal: float,
    ) -> float:
        predicted = track.predicted_bbox(timestamp)
        overlap = bbox_iou(predicted, detection.bbox)
        distance = center_distance(predicted, detection.bbox, diagonal)
        face_score = cosine_similarity(track.embedding, detection.embedding)
        body_score = cosine_similarity(track.appearance, detection.appearance)
        plausible = (
            overlap >= 0.01
            or distance <= 0.15
            or face_score >= 0.45
            or body_score >= 0.78
        )
        if not plausible:
            return 1_000.0
        cost = (1.0 - overlap) * 0.52 + min(distance / 0.16, 1.0) * 0.28
        cost += (1.0 - max(face_score, 0.0)) * 0.08 if face_score >= 0 else 0.08
        cost += (1.0 - max(body_score, 0.0)) * 0.12 if body_score >= 0 else 0.12
        return cost

    def _recovery_cost(
        self,
        track: Track,
        detection: Detection,
        timestamp: float,
        diagonal: float,
    ) -> float:
        predicted = track.predicted_bbox(timestamp)
        overlap = bbox_iou(predicted, detection.bbox)
        distance = center_distance(predicted, detection.bbox, diagonal)
        if overlap < 0.02 and distance > 0.085:
            return 1_000.0
        return (1.0 - overlap) * 0.68 + min(distance / 0.10, 1.0) * 0.32

    @staticmethod
    def _associate(
        tracks: Sequence[Track],
        detections: Sequence[Detection],
        cost_function,
        maximum_cost: float,
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        if not tracks or not detections:
            return [], list(range(len(tracks))), list(range(len(detections)))
        costs = [[cost_function(track, detection) for detection in detections] for track in tracks]
        pairs = optimal_assignment(costs, maximum_cost)
        matched_tracks = {row for row, _ in pairs}
        matched_detections = {column for _, column in pairs}
        return (
            pairs,
            [index for index in range(len(tracks)) if index not in matched_tracks],
            [index for index in range(len(detections)) if index not in matched_detections],
        )

    def _new_track(self, detection: Detection, timestamp: float) -> Track:
        track = Track(
            local_id=self.next_id,
            bbox=list(detection.bbox),
            first_seen=float(timestamp),
            last_seen=float(timestamp),
            score=float(detection.score),
            embeddings=[detection.embedding] if detection.embedding is not None else [],
            appearances=[detection.appearance] if detection.appearance is not None else [],
        )
        self.tracks[self.next_id] = track
        self.next_id += 1
        return track

    def update(
        self,
        detections: Sequence[Detection],
        timestamp: float,
        frame_shape: Sequence[int],
    ) -> Tuple[List[Track], List[Track]]:
        diagonal = self._diagonal(frame_shape)
        high = [detection for detection in detections if detection.score >= self.high_score]
        low = [detection for detection in detections if self.low_score <= detection.score < self.high_score]
        current_tracks = list(self.tracks.values())

        primary_pairs, unmatched_track_indices, unmatched_high = self._associate(
            current_tracks,
            high,
            lambda track, detection: self._primary_cost(track, detection, timestamp, diagonal),
            0.90,
        )
        matched_track_ids = set()
        active: List[Track] = []
        for track_index, detection_index in primary_pairs:
            track = current_tracks[track_index]
            track.observe(high[detection_index], timestamp)
            matched_track_ids.add(track.local_id)
            active.append(track)

        recovery_tracks = [current_tracks[index] for index in unmatched_track_indices]
        recovery_pairs, still_unmatched_tracks, _ = self._associate(
            recovery_tracks,
            low,
            lambda track, detection: self._recovery_cost(track, detection, timestamp, diagonal),
            0.84,
        )
        for track_index, detection_index in recovery_pairs:
            track = recovery_tracks[track_index]
            track.observe(low[detection_index], timestamp)
            matched_track_ids.add(track.local_id)
            active.append(track)

        for detection_index in unmatched_high:
            detection = high[detection_index]
            if detection.score >= self.new_track_score:
                track = self._new_track(detection, timestamp)
                matched_track_ids.add(track.local_id)
                active.append(track)

        unmatched_ids = {recovery_tracks[index].local_id for index in still_unmatched_tracks}
        # Tracks not observed in either association stage age by one sampled
        # frame. Their state is retained to bridge short occlusions.
        ended: List[Track] = []
        for track_id, track in list(self.tracks.items()):
            if track_id in matched_track_ids:
                continue
            if track_id in unmatched_ids or track in current_tracks:
                track.missing += 1
            if track.missing > self.max_missing:
                ended.append(self.tracks.pop(track_id))

        active.sort(key=lambda track: track.local_id)
        return active, ended

    def finish(self) -> List[Track]:
        ended = list(self.tracks.values())
        self.tracks.clear()
        return ended


class CentroidTracker(ByteTracker):
    """Backward-compatible name retained for older imports and tests."""

