from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .geometry import bbox_iou
from .tracking import optimal_assignment


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    return _ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative)


def match_observations(
    predictions: Sequence[Dict],
    truth: Sequence[Dict],
    time_tolerance: float = 0.22,
    iou_threshold: float = 0.5,
) -> List[Tuple[int, int]]:
    """Match predicted and reviewed boxes by camera, time and IoU."""

    truth_by_camera: Dict[str, List[Tuple[float, int]]] = defaultdict(list)
    for index, observation in enumerate(truth):
        truth_by_camera[str(observation.get("cameraId"))].append((float(observation.get("time", 0)), index))
    for values in truth_by_camera.values():
        values.sort()

    candidates = []
    for prediction_index, prediction in enumerate(predictions):
        rows = truth_by_camera.get(str(prediction.get("cameraId")), [])
        if not rows:
            continue
        times = [item[0] for item in rows]
        timestamp = float(prediction.get("time", 0))
        start = bisect.bisect_left(times, timestamp - time_tolerance)
        end = bisect.bisect_right(times, timestamp + time_tolerance)
        for truth_time, truth_index in rows[start:end]:
            overlap = bbox_iou(prediction.get("bbox", []), truth[truth_index].get("bbox", []))
            if overlap < iou_threshold:
                continue
            cost = (1.0 - overlap) + abs(timestamp - truth_time) / max(time_tolerance, 1e-3) * 0.1
            candidates.append((cost, prediction_index, truth_index))

    matched_predictions, matched_truth, pairs = set(), set(), []
    for _, prediction_index, truth_index in sorted(candidates):
        if prediction_index in matched_predictions or truth_index in matched_truth:
            continue
        matched_predictions.add(prediction_index)
        matched_truth.add(truth_index)
        pairs.append((prediction_index, truth_index))
    return pairs


def identity_metrics(predictions: Sequence[Dict], truth: Sequence[Dict], pairs: Iterable[Tuple[int, int]]) -> Dict:
    predicted_ids = sorted({str(row.get("globalId")) for row in predictions if row.get("globalId")})
    truth_ids = sorted({str(row.get("globalId")) for row in truth if row.get("globalId")})
    cooccurrence: Dict[Tuple[str, str], int] = defaultdict(int)
    for prediction_index, truth_index in pairs:
        predicted_id = predictions[prediction_index].get("globalId")
        truth_id = truth[truth_index].get("globalId")
        if predicted_id and truth_id:
            cooccurrence[(str(predicted_id), str(truth_id))] += 1

    maximum = max(cooccurrence.values(), default=0)
    id_true_positive = 0
    if predicted_ids and truth_ids and maximum:
        costs = [
            [float(maximum - cooccurrence[(predicted_id, truth_id)]) for truth_id in truth_ids]
            for predicted_id in predicted_ids
        ]
        for predicted_index, truth_index in optimal_assignment(costs, float(maximum)):
            id_true_positive += cooccurrence[(predicted_ids[predicted_index], truth_ids[truth_index])]
    id_false_positive = max(0, len(predictions) - id_true_positive)
    id_false_negative = max(0, len(truth) - id_true_positive)
    return {
        "idTruePositive": id_true_positive,
        "idFalsePositive": id_false_positive,
        "idFalseNegative": id_false_negative,
        "idf1": _f1(id_true_positive, id_false_positive, id_false_negative),
    }


def occupancy_metrics(predicted_sessions: Sequence[Dict], truth_sessions: Sequence[Dict]) -> Dict:
    def buckets(sessions: Sequence[Dict]) -> set:
        occupied = set()
        for session in sessions:
            table_id = session.get("tableId")
            if not table_id:
                continue
            start = math.floor(float(session.get("start", 0)))
            end = math.ceil(float(session.get("end", start)))
            occupied.update((str(table_id), second) for second in range(start, end + 1))
        return occupied

    predicted, reviewed = buckets(predicted_sessions), buckets(truth_sessions)
    true_positive = len(predicted & reviewed)
    false_positive = len(predicted - reviewed)
    false_negative = len(reviewed - predicted)
    return {
        "truePositiveSeconds": true_positive,
        "falsePositiveSeconds": false_positive,
        "falseNegativeSeconds": false_negative,
        "precision": _ratio(true_positive, true_positive + false_positive),
        "recall": _ratio(true_positive, true_positive + false_negative),
        "f1": _f1(true_positive, false_positive, false_negative),
    }


def handoff_metrics(predictions: Sequence[Dict], truth: Sequence[Dict], tolerance_seconds: float = 3.0) -> Dict:
    candidates = []
    for prediction_index, prediction in enumerate(predictions):
        for truth_index, reviewed in enumerate(truth):
            if prediction.get("fromCameraId") != reviewed.get("fromCameraId") or prediction.get("toCameraId") != reviewed.get("toCameraId"):
                continue
            difference = abs(float(prediction.get("time", 0)) - float(reviewed.get("time", 0)))
            if difference <= tolerance_seconds:
                candidates.append((difference, prediction_index, truth_index))
    used_predictions, used_truth = set(), set()
    for _, prediction_index, truth_index in sorted(candidates):
        if prediction_index in used_predictions or truth_index in used_truth:
            continue
        used_predictions.add(prediction_index)
        used_truth.add(truth_index)
    true_positive = len(used_predictions)
    false_positive = len(predictions) - true_positive
    false_negative = len(truth) - true_positive
    return {
        "truePositive": true_positive,
        "falsePositive": false_positive,
        "falseNegative": false_negative,
        "precision": _ratio(true_positive, true_positive + false_positive),
        "recall": _ratio(true_positive, true_positive + false_negative),
        "f1": _f1(true_positive, false_positive, false_negative),
    }


def first_service_error(predictions: Sequence[Dict], truth: Sequence[Dict]) -> Dict:
    errors = []
    for reviewed in truth:
        reviewed_value = reviewed.get("firstServiceSeconds")
        if reviewed_value is None:
            continue
        candidates = [
            prediction for prediction in predictions
            if prediction.get("tableId") == reviewed.get("tableId")
            and prediction.get("firstServiceSeconds") is not None
            and float(prediction.get("end", 0)) >= float(reviewed.get("start", 0))
            and float(prediction.get("start", 0)) <= float(reviewed.get("end", 0))
        ]
        if not candidates:
            continue
        prediction = min(candidates, key=lambda row: abs(float(row.get("start", 0)) - float(reviewed.get("start", 0))))
        errors.append(abs(float(prediction["firstServiceSeconds"]) - float(reviewed_value)))
    return {
        "matchedSessions": len(errors),
        "meanAbsoluteErrorSeconds": round(sum(errors) / len(errors), 3) if errors else None,
        "maximumAbsoluteErrorSeconds": round(max(errors), 3) if errors else None,
    }


def evaluate(prediction: Dict, truth: Dict) -> Dict:
    predicted_observations = prediction.get("observations", [])
    truth_observations = truth.get("observations", [])
    pairs = match_observations(predicted_observations, truth_observations)
    true_positive = len(pairs)
    false_positive = len(predicted_observations) - true_positive
    false_negative = len(truth_observations) - true_positive
    return {
        "schema": "serviceflow-evaluation/v1",
        "detection": {
            "truePositive": true_positive,
            "falsePositive": false_positive,
            "falseNegative": false_negative,
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "f1": _f1(true_positive, false_positive, false_negative),
        },
        "identity": identity_metrics(predicted_observations, truth_observations, pairs),
        "tableOccupancy": occupancy_metrics(prediction.get("sessions", []), truth.get("sessions", [])),
        "cameraHandoffs": handoff_metrics(prediction.get("handoffs", []), truth.get("handoffs", [])),
        "firstService": first_service_error(prediction.get("sessions", []), truth.get("sessions", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a ServiceFlow result against reviewed ground truth")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("truth", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    report = evaluate(prediction, truth)
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
