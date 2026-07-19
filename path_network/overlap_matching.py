from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from path_network.geometry import (
    coordinate_at_position,
    distance,
    interpolate_coordinate,
    project_point_to_leg,
    projector_for,
)


@dataclass(frozen=True)
class OverlapMatchingConfig:
    version: str = "diagnostics-v1"
    search_distance_metres: float = 15.0
    sample_interval_metres: float = 5.0
    minimum_overlap_metres: float = 30.0
    high_confidence_median_separation_metres: float = 4.0
    high_confidence_p90_separation_metres: float = 6.0
    reviewable_maximum_separation_metres: float = 10.0
    maximum_direction_difference_degrees: float = 25.0
    minimum_high_confidence_coverage: float = 0.85
    ambiguity_margin_metres: float = 2.0
    minimum_branch_distance_metres: float = 20.0


DEFAULT_OVERLAP_CONFIG = OverlapMatchingConfig()


def _segment_identifier(segment: dict[str, Any]) -> str | int:
    return segment["id"]


def _junction_identifier(segment: dict[str, Any], end: str) -> str | int | None:
    return segment.get(
        f"{end}JunctionId",
        segment.get(f"{end}_junction_id"),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _angle_difference_degrees(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    first_angle = math.atan2(first[1], first[0])
    second_angle = math.atan2(second[1], second[0])
    difference = abs(math.degrees(first_angle - second_angle)) % 180.0
    return min(difference, 180.0 - difference)


def _geometry_length(projected: list[tuple[float, float]]) -> float:
    return sum(distance(start, end) for start, end in zip(projected, projected[1:]))


def _sample_geometry(
    geometry: list[list[float]],
    projected: list[tuple[float, float]],
    interval_metres: float,
) -> list[dict[str, Any]]:
    leg_lengths = [
        distance(start, end) for start, end in zip(projected, projected[1:])
    ]
    total_length = sum(leg_lengths)
    if total_length == 0:
        return []
    targets = [
        min(index * interval_metres, total_length)
        for index in range(math.ceil(total_length / interval_metres) + 1)
    ]
    if targets[-1] < total_length:
        targets.append(total_length)

    samples: list[dict[str, Any]] = []
    leg_index = 0
    traversed = 0.0
    for target in targets:
        while (
            leg_index < len(leg_lengths) - 1
            and traversed + leg_lengths[leg_index] < target
        ):
            traversed += leg_lengths[leg_index]
            leg_index += 1
        leg_length = leg_lengths[leg_index]
        fraction = 0.0 if leg_length == 0 else (target - traversed) / leg_length
        fraction = min(1.0, max(0.0, fraction))
        projected_coordinate = (
            projected[leg_index][0]
            + (projected[leg_index + 1][0] - projected[leg_index][0]) * fraction,
            projected[leg_index][1]
            + (projected[leg_index + 1][1] - projected[leg_index][1]) * fraction,
        )
        direction = (
            projected[leg_index + 1][0] - projected[leg_index][0],
            projected[leg_index + 1][1] - projected[leg_index][1],
        )
        samples.append(
            {
                "distance": target,
                "position": leg_index + fraction,
                "coordinate": interpolate_coordinate(
                    geometry[leg_index],
                    geometry[leg_index + 1],
                    fraction,
                ),
                "projected": projected_coordinate,
                "direction": direction,
            }
        )
    return samples


def _projection_options(
    sample: dict[str, Any],
    saved_segments: list[dict[str, Any]],
    config: OverlapMatchingConfig,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for segment in saved_segments:
        projected_geometry = segment["_overlapProjected"]
        leg_lengths = segment["_overlapLegLengths"]
        cumulative = 0.0
        for leg_index, (start, end) in enumerate(
            zip(projected_geometry, projected_geometry[1:])
        ):
            fraction, projected, separation = project_point_to_leg(
                sample["projected"],
                start,
                end,
            )
            if separation > config.search_distance_metres:
                cumulative += leg_lengths[leg_index]
                continue
            saved_direction = (end[0] - start[0], end[1] - start[1])
            direction_difference = _angle_difference_degrees(
                sample["direction"],
                saved_direction,
            )
            options.append(
                {
                    "segmentId": _segment_identifier(segment),
                    "segmentPosition": leg_index + fraction,
                    "distanceAlongSegment": cumulative
                    + leg_lengths[leg_index] * fraction,
                    "segmentLength": segment["_overlapLength"],
                    "projectedCoordinate": [projected[0], projected[1]],
                    "separation": separation,
                    "directionDifference": direction_difference,
                }
            )
            cumulative += leg_lengths[leg_index]
    return sorted(
        options,
        key=lambda option: (
            option["separation"],
            option["directionDifference"],
            str(option["segmentId"]),
            option["segmentPosition"],
        ),
    )


def _segments_share_junction(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    first_ids = {
        _junction_identifier(first, "start"),
        _junction_identifier(first, "end"),
    }
    second_ids = {
        _junction_identifier(second, "start"),
        _junction_identifier(second, "end"),
    }
    first_ids.discard(None)
    second_ids.discard(None)
    return bool(first_ids & second_ids)


def _transition_cost(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    segments_by_id: dict[str | int, dict[str, Any]],
    sample_step: float,
) -> float:
    if previous is None and current is None:
        return 0.0
    if previous is None or current is None:
        return 3.0
    if previous["segmentId"] == current["segmentId"]:
        movement = abs(
            current["distanceAlongSegment"] - previous["distanceAlongSegment"]
        )
        return max(0.0, movement - sample_step * 2.5) * 0.4
    if _segments_share_junction(
        segments_by_id[previous["segmentId"]],
        segments_by_id[current["segmentId"]],
    ):
        previous_endpoint_distance = min(
            previous["distanceAlongSegment"],
            previous["segmentLength"] - previous["distanceAlongSegment"],
        )
        current_endpoint_distance = min(
            current["distanceAlongSegment"],
            current["segmentLength"] - current["distanceAlongSegment"],
        )
        if previous_endpoint_distance <= sample_step * 2 and current_endpoint_distance <= sample_step * 2:
            return 2.0
    return 20.0


def _choose_continuous_options(
    samples: list[dict[str, Any]],
    saved_segments: list[dict[str, Any]],
    config: OverlapMatchingConfig,
) -> list[dict[str, Any] | None]:
    segments_by_id = {
        _segment_identifier(segment): segment for segment in saved_segments
    }
    state_rows: list[list[dict[str, Any] | None]] = []
    costs: list[dict[int, tuple[float, int | None]]] = []
    for sample_index, sample in enumerate(samples):
        options = [
            option
            for option in _projection_options(sample, saved_segments, config)
            if option["directionDifference"] <= 90.0
        ]
        states: list[dict[str, Any] | None] = [None, *options]
        state_rows.append(states)
        row_costs: dict[int, tuple[float, int | None]] = {}
        for state_index, state in enumerate(states):
            observation_cost = (
                config.reviewable_maximum_separation_metres
                if state is None
                else state["separation"]
                + max(
                    0.0,
                    state["directionDifference"]
                    - config.maximum_direction_difference_degrees,
                )
                * 0.2
            )
            if sample_index == 0:
                row_costs[state_index] = (observation_cost, None)
                continue
            previous_states = state_rows[sample_index - 1]
            previous_costs = costs[sample_index - 1]
            candidates = [
                (
                    previous_costs[previous_index][0]
                    + _transition_cost(
                        previous_state,
                        state,
                        segments_by_id,
                        config.sample_interval_metres,
                    )
                    + observation_cost,
                    previous_index,
                )
                for previous_index, previous_state in enumerate(previous_states)
            ]
            row_costs[state_index] = min(candidates, key=lambda candidate: candidate[0])
        costs.append(row_costs)

    final_state = min(costs[-1], key=lambda index: costs[-1][index][0])
    selected: list[dict[str, Any] | None] = []
    for sample_index in range(len(samples) - 1, -1, -1):
        selected.append(state_rows[sample_index][final_state])
        previous = costs[sample_index][final_state][1]
        if previous is None:
            break
        final_state = previous
    selected.reverse()
    return selected


def _monotonicity(
    selected: list[dict[str, Any] | None],
) -> float:
    comparable = [
        (first, second)
        for first, second in zip(selected, selected[1:])
        if first is not None
        and second is not None
        and first["segmentId"] == second["segmentId"]
    ]
    if not comparable:
        return 1.0
    deltas = [
        second["distanceAlongSegment"] - first["distanceAlongSegment"]
        for first, second in comparable
        if abs(second["distanceAlongSegment"] - first["distanceAlongSegment"]) > 0.1
    ]
    if not deltas:
        return 1.0
    positive = sum(delta > 0 for delta in deltas)
    negative = len(deltas) - positive
    return max(positive, negative) / len(deltas)


def _candidate_key(candidate: dict[str, Any]) -> str:
    payload = {
        "section": candidate["sectionNumber"],
        "start": round(candidate["uploadedStartPosition"], 3),
        "end": round(candidate["uploadedEndPosition"], 3),
        "segments": candidate["savedPathSegmentIds"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def _boundary_type(
    *,
    is_start: bool,
    unmatched_distance: float,
    minimum_branch_distance: float,
) -> str:
    if unmatched_distance < minimum_branch_distance:
        return "section_endpoint"
    return "join" if is_start else "branch"


def _derive_candidates(
    section: dict[str, Any],
    samples: list[dict[str, Any]],
    selected: list[dict[str, Any] | None],
    all_options: list[list[dict[str, Any]]],
    config: OverlapMatchingConfig,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    run_start: int | None = None
    for index in range(len(samples) + 1):
        matched = index < len(samples) and selected[index] is not None
        if matched and run_start is None:
            run_start = index
        if matched or run_start is None:
            continue
        run_end = index - 1
        overlap_length = samples[run_end]["distance"] - samples[run_start]["distance"]
        if overlap_length < config.minimum_overlap_metres:
            run_start = None
            continue
        run_selected = selected[run_start : run_end + 1]
        separations = [option["separation"] for option in run_selected if option]
        direction_differences = [
            option["directionDifference"] for option in run_selected if option
        ]
        segment_ids: list[str | int] = []
        for option in run_selected:
            if option and (
                not segment_ids or segment_ids[-1] != option["segmentId"]
            ):
                segment_ids.append(option["segmentId"])
        ambiguous_samples = 0
        for options in all_options[run_start : run_end + 1]:
            if len(options) >= 2 and (
                options[1]["separation"] - options[0]["separation"]
                <= config.ambiguity_margin_metres
            ):
                ambiguous_samples += 1
        ambiguity_ratio = ambiguous_samples / max(1, len(run_selected))
        coverage = overlap_length / max(samples[-1]["distance"], 1.0)
        median_separation = median(separations)
        p90_separation = _percentile(separations, 0.9)
        p90_direction = _percentile(direction_differences, 0.9)
        monotonicity = _monotonicity(run_selected)
        if (
            median_separation
            <= config.high_confidence_median_separation_metres
            and p90_separation <= config.high_confidence_p90_separation_metres
            and p90_direction <= config.maximum_direction_difference_degrees
            and monotonicity >= 0.9
            and ambiguity_ratio < 0.1
            and coverage >= config.minimum_high_confidence_coverage
        ):
            confidence = "high"
        elif p90_separation <= config.search_distance_metres and monotonicity >= 0.7:
            confidence = "medium"
        else:
            confidence = "low"
        start_unmatched = samples[run_start]["distance"]
        end_unmatched = samples[-1]["distance"] - samples[run_end]["distance"]
        candidate = {
            "sectionNumber": section["sectionNumber"],
            "uploadedStartPosition": round(samples[run_start]["position"], 6),
            "uploadedEndPosition": round(samples[run_end]["position"], 6),
            "uploadedStartCoordinate": samples[run_start]["coordinate"],
            "uploadedEndCoordinate": samples[run_end]["coordinate"],
            "uploadedGeometry": [
                sample["coordinate"] for sample in samples[run_start : run_end + 1]
            ],
            "savedPathSegmentIds": segment_ids,
            "savedStartPathSegmentId": run_selected[0]["segmentId"],
            "savedStartPosition": round(
                run_selected[0]["segmentPosition"],
                6,
            ),
            "savedStartProjectedCoordinate": run_selected[0][
                "projectedCoordinate"
            ],
            "savedEndPathSegmentId": run_selected[-1]["segmentId"],
            "savedEndPosition": round(
                run_selected[-1]["segmentPosition"],
                6,
            ),
            "savedEndProjectedCoordinate": run_selected[-1][
                "projectedCoordinate"
            ],
            "lengthMetres": round(overlap_length),
            "coverage": round(coverage, 4),
            "medianSeparationMetres": round(median_separation, 2),
            "p90SeparationMetres": round(p90_separation, 2),
            "maximumSeparationMetres": round(max(separations), 2),
            "p90DirectionDifferenceDegrees": round(p90_direction, 2),
            "monotonicity": round(monotonicity, 4),
            "ambiguityRatio": round(ambiguity_ratio, 4),
            "confidence": confidence,
            "startBoundary": {
                "type": _boundary_type(
                    is_start=True,
                    unmatched_distance=start_unmatched,
                    minimum_branch_distance=config.minimum_branch_distance_metres,
                ),
                "unmatchedDistanceMetres": round(start_unmatched),
            },
            "endBoundary": {
                "type": _boundary_type(
                    is_start=False,
                    unmatched_distance=end_unmatched,
                    minimum_branch_distance=config.minimum_branch_distance_metres,
                ),
                "unmatchedDistanceMetres": round(end_unmatched),
            },
        }
        candidate["key"] = _candidate_key(candidate)
        candidates.append(candidate)
        run_start = None
    return candidates


def analyze_trace_overlaps(
    sections: list[dict[str, Any]],
    saved_segments: list[dict[str, Any]],
    *,
    config: OverlapMatchingConfig = DEFAULT_OVERLAP_CONFIG,
) -> dict[str, Any]:
    if not sections or not saved_segments:
        return {
            "config": asdict(config),
            "candidates": [],
            "summary": {
                "candidateCount": 0,
                "highConfidenceCount": 0,
                "mediumConfidenceCount": 0,
                "lowConfidenceCount": 0,
                "candidateDistanceMetres": 0,
            },
        }

    projector = projector_for(
        [section["geometry"] for section in sections]
        + [segment["geometry"] for segment in saved_segments]
    )
    prepared_segments = []
    for source in saved_segments:
        segment = dict(source)
        projected = [projector.project(point) for point in segment["geometry"]]
        segment["_overlapProjected"] = projected
        segment["_overlapLegLengths"] = [
            distance(start, end) for start, end in zip(projected, projected[1:])
        ]
        segment["_overlapLength"] = _geometry_length(projected)
        prepared_segments.append(segment)

    candidates: list[dict[str, Any]] = []
    for section in sections:
        projected = [projector.project(point) for point in section["geometry"]]
        samples = _sample_geometry(
            section["geometry"],
            projected,
            config.sample_interval_metres,
        )
        if not samples:
            continue
        all_options = [
            _projection_options(sample, prepared_segments, config)
            for sample in samples
        ]
        selected = _choose_continuous_options(
            samples,
            prepared_segments,
            config,
        )
        candidates.extend(
            _derive_candidates(
                section,
                samples,
                selected,
                all_options,
                config,
            )
        )
    segments_by_id = {
        _segment_identifier(segment): segment for segment in prepared_segments
    }
    for candidate in candidates:
        chain = candidate["savedPathSegmentIds"]
        candidate["savedChainContinuous"] = all(
            _segments_share_junction(
                segments_by_id[first_id],
                segments_by_id[second_id],
            )
            for first_id, second_id in zip(chain, chain[1:])
        )
        for boundary_name in ("Start", "End"):
            segment = segments_by_id[
                candidate[f"saved{boundary_name}PathSegmentId"]
            ]
            position = candidate[f"saved{boundary_name}Position"]
            coordinate = projector.unproject(
                candidate[f"saved{boundary_name}ProjectedCoordinate"]
            )
            source_coordinate = coordinate_at_position(
                segment["geometry"],
                position,
            )
            if len(source_coordinate) == 3:
                coordinate.append(source_coordinate[2])
            candidate[f"saved{boundary_name}Coordinate"] = coordinate

    return {
        "config": asdict(config),
        "candidates": candidates,
        "summary": {
            "candidateCount": len(candidates),
            "highConfidenceCount": sum(
                candidate["confidence"] == "high" for candidate in candidates
            ),
            "mediumConfidenceCount": sum(
                candidate["confidence"] == "medium" for candidate in candidates
            ),
            "lowConfidenceCount": sum(
                candidate["confidence"] == "low" for candidate in candidates
            ),
            "candidateDistanceMetres": sum(
                candidate["lengthMetres"] for candidate in candidates
            ),
        },
    }
