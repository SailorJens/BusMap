from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from path_network.geometry import (
    INTERSECTION_EPSILON,
    coordinate_at_position,
    distance,
    expanded_wgs84_bounds,
    geometry_key,
    interpolate_coordinate,
    leg_intersection,
    position_on_geometry,
    project_point_to_geometry,
    project_point_to_leg,
    projector_for,
    remove_consecutive_duplicates,
    split_geometry,
)
from path_network.gpx import haversine_distance
from path_network.overlap_matching import (
    DEFAULT_OVERLAP_CONFIG,
    analyze_trace_overlaps,
)
from path_network.repository import (
    list_junctions_in_bounds,
    list_path_segments_in_bounds,
)


INTERSECTION_TOLERANCE_METRES = 10.0
class ImportDraftError(ValueError):
    pass


def _segment_distance(geometry: Sequence[Sequence[float]]) -> int:
    return round(
        sum(
            haversine_distance(list(previous), list(current))
            for previous, current in zip(geometry, geometry[1:])
        )
    )


def _bounds(geometry: Sequence[Sequence[float]]) -> list[float]:
    return [
        min(float(coordinate[0]) for coordinate in geometry),
        min(float(coordinate[1]) for coordinate in geometry),
        max(float(coordinate[0]) for coordinate in geometry),
        max(float(coordinate[1]) for coordinate in geometry),
    ]


def _node_coordinate(coordinate: Sequence[float]) -> list[float]:
    return list(map(float, coordinate))


def _geometry_interval(
    geometry: Sequence[Sequence[float]],
    start_position: float,
    end_position: float,
    *,
    start_coordinate: Sequence[float] | None = None,
    end_coordinate: Sequence[float] | None = None,
) -> list[list[float]] | None:
    if end_position - start_position < 1e-9:
        return None
    start = (
        list(map(float, start_coordinate))
        if start_coordinate is not None
        else coordinate_at_position(geometry, start_position)
    )
    end = (
        list(map(float, end_coordinate))
        if end_coordinate is not None
        else coordinate_at_position(geometry, end_position)
    )
    result = [start]
    for vertex_index in range(
        math.floor(start_position) + 1,
        math.ceil(end_position),
    ):
        coordinate = list(map(float, geometry[vertex_index]))
        if coordinate[:2] != result[-1][:2]:
            result.append(coordinate)
    if end[:2] != result[-1][:2]:
        result.append(end)
    if len(result) < 2 or len({tuple(point[:2]) for point in result}) < 2:
        return None
    return result


def _path_segment(
    identifier: str,
    piece: dict[str, Any],
    *,
    source_filename: str | None,
    origin: str,
    replaces_path_segment_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    geometry = piece["geometry"]
    result = {
        "id": identifier,
        "startJunctionId": piece["startNode"]["id"],
        "endJunctionId": piece["endNode"]["id"],
        "geometry": geometry,
        "distance_m": _segment_distance(geometry),
        "bounds": _bounds(geometry),
        "sourceFilename": source_filename,
        "metadata": metadata or {},
        "origin": origin,
        "state": "added",
    }
    if replaces_path_segment_id is not None:
        result["replacesPathSegmentId"] = replaces_path_segment_id
    return result


def _review_type_for_candidate(candidate: dict[str, Any]) -> str:
    if (
        candidate["coverage"]
        >= DEFAULT_OVERLAP_CONFIG.minimum_high_confidence_coverage
        and candidate["startBoundary"]["type"] == "section_endpoint"
        and candidate["endBoundary"]["type"] == "section_endpoint"
        and candidate["savedChainContinuous"]
    ):
        return "complete_section_reuse"
    if (
        candidate["savedChainContinuous"]
        and (
            candidate["startBoundary"]["type"] in {"join", "section_endpoint"}
            or candidate["endBoundary"]["type"] in {"branch", "section_endpoint"}
        )
        and (
            candidate["startBoundary"]["type"] != "section_endpoint"
            or candidate["endBoundary"]["type"] != "section_endpoint"
        )
    ):
        return "partial_section_reuse"
    return "diagnostic_only"


def _automatic_overlap_decision(
    candidate: dict[str, Any],
) -> str | None:
    if candidate["reviewType"] == "diagnostic_only":
        return None
    if candidate["confidence"] == "low":
        return "keep"
    if candidate["confidence"] in {"high", "medium"}:
        return "reuse"
    return None


def _segment_junction_id(segment: dict[str, Any], end: str) -> str | int | None:
    return segment.get(f"{end}_junction_id", segment.get(f"{end}JunctionId"))


def _apply_boundary_adjustment(
    candidate: dict[str, Any],
    adjustment: dict[str, Any],
    section: dict[str, Any],
    saved_segments_by_id: dict[str | int, dict[str, Any]],
    saved_junctions: Sequence[dict[str, Any]],
) -> None:
    boundary = adjustment.get("boundary")
    if boundary not in {"start", "end"}:
        raise ImportDraftError("Overlap boundary must be start or end.")

    longitude = float(adjustment["longitude"])
    latitude = float(adjustment["latitude"])
    upload_position, uploaded_coordinate, _upload_separation = project_point_to_geometry(
        [longitude, latitude],
        section["geometry"],
    )
    last_position = len(section["geometry"]) - 1.0
    upload_position = min(last_position, max(0.0, upload_position))
    saved_projection = _project_boundary_to_saved_chain(
        uploaded_coordinate,
        candidate["savedPathSegmentIds"],
        saved_segments_by_id,
        saved_junctions,
    )

    if boundary == "start":
        candidate["uploadedStartPosition"] = round(upload_position, 6)
        candidate["uploadedStartCoordinate"] = uploaded_coordinate
        candidate["savedStartPathSegmentId"] = saved_projection["segmentId"]
        candidate["savedStartPosition"] = saved_projection["position"]
        candidate["savedStartCoordinate"] = saved_projection["coordinate"]
        candidate["savedStartProjectedCoordinate"] = saved_projection[
            "projectedCoordinate"
        ]
        candidate["startBoundary"] = {
            **candidate["startBoundary"],
            "type": (
                "section_endpoint"
                if upload_position <= 1e-6
                else "join"
            ),
            "adjusted": True,
        }
    else:
        candidate["uploadedEndPosition"] = round(upload_position, 6)
        candidate["uploadedEndCoordinate"] = uploaded_coordinate
        candidate["savedEndPathSegmentId"] = saved_projection["segmentId"]
        candidate["savedEndPosition"] = saved_projection["position"]
        candidate["savedEndCoordinate"] = saved_projection["coordinate"]
        candidate["savedEndProjectedCoordinate"] = saved_projection[
            "projectedCoordinate"
        ]
        candidate["endBoundary"] = {
            **candidate["endBoundary"],
            "type": (
                "section_endpoint"
                if upload_position >= last_position - 1e-6
                else "branch"
            ),
            "adjusted": True,
        }
    candidate["hasBoundaryAdjustment"] = True


def _project_boundary_to_saved_chain(
    coordinate: Sequence[float],
    saved_path_segment_ids: Sequence[str | int],
    saved_segments_by_id: dict[str | int, dict[str, Any]],
    saved_junctions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    candidates = []
    for segment_id in saved_path_segment_ids:
        segment = saved_segments_by_id[segment_id]
        position, projected_coordinate, separation = project_point_to_geometry(
            coordinate,
            segment["geometry"],
        )
        candidates.append((separation, str(segment_id), segment, position, projected_coordinate))
    separation, _sort_id, segment, position, projected_coordinate = min(
        candidates,
        key=lambda item: (item[0], item[1], item[3]),
    )
    if separation > DEFAULT_OVERLAP_CONFIG.search_distance_metres:
        raise ImportDraftError("Adjusted overlap boundary is too far from the saved path.")

    snapped = _snap_saved_boundary_to_junction(
        projected_coordinate,
        segment,
        position,
        saved_junctions,
    )
    if snapped:
        projected_coordinate, position = snapped
    return {
        "segmentId": segment["id"],
        "position": round(position, 6),
        "coordinate": projected_coordinate,
        "projectedCoordinate": projected_coordinate[:2],
    }


def _snap_saved_boundary_to_junction(
    coordinate: Sequence[float],
    segment: dict[str, Any],
    position: float,
    saved_junctions: Sequence[dict[str, Any]],
) -> tuple[list[float], float] | None:
    junctions_by_id = {junction["id"]: junction for junction in saved_junctions}
    endpoints = [
        (_segment_junction_id(segment, "start"), 0.0),
        (_segment_junction_id(segment, "end"), len(segment["geometry"]) - 1.0),
    ]
    projector = projector_for([segment["geometry"], [coordinate]])
    projected_coordinate = projector.project(coordinate)
    candidates = []
    for junction_id, endpoint_position in endpoints:
        junction = junctions_by_id.get(junction_id)
        if junction is None:
            continue
        junction_coordinate = [junction["longitude"], junction["latitude"]]
        if junction.get("elevation") is not None:
            junction_coordinate.append(junction["elevation"])
        separation = distance(projected_coordinate, projector.project(junction_coordinate))
        if separation <= INTERSECTION_TOLERANCE_METRES:
            candidates.append((separation, abs(position - endpoint_position), junction_coordinate, endpoint_position))
    if not candidates:
        return None
    _separation, _position_delta, junction_coordinate, endpoint_position = min(candidates)
    return junction_coordinate, endpoint_position


def _apply_overlap_adjustments(
    candidates: Sequence[dict[str, Any]],
    sections_by_number: dict[int, dict[str, Any]],
    saved_segments: Sequence[dict[str, Any]],
    saved_junctions: Sequence[dict[str, Any]],
    overlap_adjustments: dict[str, list[dict[str, Any]]],
) -> None:
    saved_segments_by_id = {segment["id"]: segment for segment in saved_segments}
    for candidate in candidates:
        adjustments = overlap_adjustments.get(candidate["key"], [])
        if not adjustments:
            continue
        section = sections_by_number[candidate["sectionNumber"]]
        for adjustment in adjustments:
            _apply_boundary_adjustment(
                candidate,
                adjustment,
                section,
                saved_segments_by_id,
                saved_junctions,
            )
        if candidate["uploadedStartPosition"] >= candidate["uploadedEndPosition"] - 1e-6:
            raise ImportDraftError("Adjusted overlap boundaries must keep start before end.")
        start_position = candidate["uploadedStartPosition"]
        end_position = candidate["uploadedEndPosition"]
        candidate["uploadedGeometry"] = _geometry_interval(
            section["geometry"],
            start_position,
            end_position,
            start_coordinate=candidate["uploadedStartCoordinate"],
            end_coordinate=candidate["uploadedEndCoordinate"],
        ) or [
            candidate["uploadedStartCoordinate"],
            candidate["uploadedEndCoordinate"],
        ]
        candidate["adjustments"] = adjustments


def _validated_reuse_intervals(
    review_candidates: Sequence[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    accepted_by_section: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in review_candidates:
        if candidate["decision"] == "reuse":
            accepted_by_section[candidate["sectionNumber"]].append(candidate)

    for section_number, candidates in accepted_by_section.items():
        candidates.sort(
            key=lambda candidate: (
                candidate["uploadedStartPosition"],
                candidate["uploadedEndPosition"],
                candidate["key"],
            )
        )
        for previous, current in zip(candidates, candidates[1:]):
            if (
                current["uploadedStartPosition"]
                < previous["uploadedEndPosition"] - 1e-6
            ):
                raise ImportDraftError(
                    f"Overlap decisions for track section {section_number} "
                    "reuse overlapping intervals."
                )
    return accepted_by_section


def build_import_draft(
    parsed_gpx: dict[str, Any],
    *,
    saved_network: dict[str, Any] | None = None,
    overlap_decisions: dict[str, str] | None = None,
    overlap_adjustments: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    overlap_decisions = overlap_decisions or {}
    overlap_adjustments = overlap_adjustments or {}
    sections: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    section_geometry_keys: set[tuple[tuple[float, ...], ...]] = set()

    for section_number, source_geometry in enumerate(parsed_gpx["segments"], start=1):
        geometry = remove_consecutive_duplicates(source_geometry)
        if len(geometry) < 2 or len({tuple(point[:2]) for point in geometry}) < 2:
            raise ImportDraftError(
                f"Track section {section_number} needs at least two distinct track points."
            )
        key = geometry_key(geometry)
        if key in section_geometry_keys:
            skipped_duplicates.append(
                {"section": section_number, "reason": "Exact duplicate geometry"}
            )
            continue
        section_geometry_keys.add(key)
        sections.append(
            {
                "id": section_number - 1,
                "sectionNumber": section_number,
                "geometry": geometry,
            }
        )

    if not sections and not skipped_duplicates:
        raise ImportDraftError("The GPX file did not produce any path segments.")

    upload_geometry = [
        coordinate
        for section in sections
        for coordinate in section["geometry"]
    ]
    query_bounds = expanded_wgs84_bounds(
        upload_geometry,
        max(
            INTERSECTION_TOLERANCE_METRES,
            DEFAULT_OVERLAP_CONFIG.search_distance_metres,
        ),
    )
    if saved_network is None:
        saved_segments = list_path_segments_in_bounds(*query_bounds)
        saved_junctions = list_junctions_in_bounds(*query_bounds)
    else:
        min_longitude, min_latitude, max_longitude, max_latitude = query_bounds
        saved_segments = [
            dict(segment)
            for segment in saved_network["pathSegments"]
            if segment["bounds_max_lon"] >= min_longitude
            and segment["bounds_min_lon"] <= max_longitude
            and segment["bounds_max_lat"] >= min_latitude
            and segment["bounds_min_lat"] <= max_latitude
        ]
        saved_junctions = [
            dict(junction)
            for junction in saved_network["junctions"]
            if min_longitude <= junction["longitude"] <= max_longitude
            and min_latitude <= junction["latitude"] <= max_latitude
        ]
    saved_geometry_keys = {geometry_key(segment["geometry"]) for segment in saved_segments}

    filtered_sections: list[dict[str, Any]] = []
    for section in sections:
        if geometry_key(section["geometry"]) in saved_geometry_keys:
            skipped_duplicates.append(
                {
                    "section": section["sectionNumber"],
                    "reason": "Exact duplicate geometry",
                }
            )
        else:
            filtered_sections.append(section)
    sections = filtered_sections
    overlap_analysis = analyze_trace_overlaps(sections, saved_segments)
    sections_by_number = {
        section["sectionNumber"]: section for section in sections
    }
    _apply_overlap_adjustments(
        overlap_analysis["candidates"],
        sections_by_number,
        saved_segments,
        saved_junctions,
        overlap_adjustments,
    )
    review_candidates = []
    for candidate in overlap_analysis["candidates"]:
        candidate["reviewType"] = _review_type_for_candidate(candidate)
        automatic_decision = _automatic_overlap_decision(candidate)
        candidate["automaticDecision"] = automatic_decision
        candidate["decisionSource"] = None
        candidate["decision"] = None
        if candidate["reviewType"] != "diagnostic_only":
            if candidate["key"] in overlap_decisions:
                candidate["decision"] = overlap_decisions[candidate["key"]]
                candidate["decisionSource"] = "user"
            elif automatic_decision:
                candidate["decision"] = automatic_decision
                candidate["decisionSource"] = "automatic"
        if candidate["reviewType"] != "diagnostic_only":
            review_candidates.append(candidate)

    review_keys = {candidate["key"] for candidate in review_candidates}
    unknown_decisions = sorted(set(overlap_decisions) - review_keys)
    if unknown_decisions:
        raise ImportDraftError(
            "Overlap decision does not match a reviewable candidate: "
            f"{unknown_decisions[0]}"
        )
    unknown_adjustments = sorted(set(overlap_adjustments) - review_keys)
    if unknown_adjustments:
        raise ImportDraftError(
            "Overlap adjustment does not match a reviewable candidate: "
            f"{unknown_adjustments[0]}"
        )
    invalid_decisions = sorted(
        {
            decision
            for decision in overlap_decisions.values()
            if decision not in {"reuse", "keep"}
        }
    )
    if invalid_decisions:
        raise ImportDraftError("Overlap decisions must be reuse or keep.")

    sections_before_reuse = sections
    accepted_by_section = _validated_reuse_intervals(review_candidates)
    transformed_sections: list[dict[str, Any]] = []
    reused_distance = 0
    for section in sections_before_reuse:
        accepted_candidates = accepted_by_section.get(section["sectionNumber"], [])
        if not accepted_candidates:
            transformed_sections.append(section)
            continue
        source_distance = _segment_distance(section["geometry"])
        if (
            len(accepted_candidates) == 1
            and accepted_candidates[0]["reviewType"] == "complete_section_reuse"
        ):
            reused_distance += source_distance
            continue

        last_position = len(section["geometry"]) - 1.0
        pieces: list[list[list[float]]] = []
        cursor_position = 0.0
        cursor_coordinate: Sequence[float] | None = None
        for candidate in accepted_candidates:
            start_position = (
                0.0
                if candidate["startBoundary"]["type"] == "section_endpoint"
                else candidate["uploadedStartPosition"]
            )
            end_position = (
                last_position
                if candidate["endBoundary"]["type"] == "section_endpoint"
                else candidate["uploadedEndPosition"]
            )
            if start_position < cursor_position - 1e-6:
                raise ImportDraftError(
                    f"Overlap decisions for track section {section['sectionNumber']} "
                    "reuse contradictory intervals."
                )
            piece = _geometry_interval(
                section["geometry"],
                cursor_position,
                start_position,
                start_coordinate=cursor_coordinate,
                end_coordinate=candidate["savedStartCoordinate"],
            )
            if piece:
                pieces.append(piece)
            cursor_position = end_position
            cursor_coordinate = candidate["savedEndCoordinate"]
        suffix = _geometry_interval(
            section["geometry"],
            cursor_position,
            last_position,
            start_coordinate=cursor_coordinate,
        )
        if suffix:
            pieces.append(suffix)
        for geometry in pieces:
            transformed_sections.append(
                {
                    "id": 0,
                    "sectionNumber": section["sectionNumber"],
                    "geometry": geometry,
                }
            )
        reused_distance += max(
            0,
            source_distance - sum(_segment_distance(piece) for piece in pieces),
        )
    sections = transformed_sections
    for section_id, section in enumerate(sections):
        section["id"] = section_id
    new_distance = sum(_segment_distance(section["geometry"]) for section in sections)
    automatic_candidates = [
        candidate
        for candidate in review_candidates
        if candidate["decisionSource"] == "automatic"
    ]
    overlap_analysis["summary"].update(
        {
            "reviewCandidateCount": len(review_candidates),
            "unresolvedCount": sum(
                candidate["decision"] not in {"reuse", "keep"}
                for candidate in review_candidates
            ),
            "reusedDistanceMetres": reused_distance,
            "newDistanceMetres": new_distance,
            "keptCandidateCount": sum(
                candidate["decision"] == "keep"
                for candidate in review_candidates
            ),
            "automaticReuseCount": sum(
                candidate["decision"] == "reuse"
                for candidate in automatic_candidates
            ),
            "automaticKeepCount": sum(
                candidate["decision"] == "keep"
                for candidate in automatic_candidates
            ),
        }
    )
    overlap_analysis["automaticDecisions"] = [
        {
            "candidateKey": candidate["key"],
            "decision": candidate["decision"],
            "confidence": candidate["confidence"],
            "reviewType": candidate["reviewType"],
            "lengthMetres": candidate["lengthMetres"],
            "medianSeparationMetres": candidate["medianSeparationMetres"],
            "p90SeparationMetres": candidate["p90SeparationMetres"],
            "ambiguityRatio": candidate["ambiguityRatio"],
            "monotonicity": candidate["monotonicity"],
            "savedPathSegmentIds": candidate["savedPathSegmentIds"],
        }
        for candidate in automatic_candidates
    ]
    overlap_analysis["hasUnresolvedOverlaps"] = (
        overlap_analysis["summary"]["unresolvedCount"] > 0
    )

    if not sections:
        return {
            "name": parsed_gpx["name"],
            "filename": parsed_gpx["filename"],
            "junctions": [],
            "pathSegments": [],
            "replacedPathSegmentIds": [],
            "skippedDuplicates": skipped_duplicates,
            "stats": parsed_gpx["stats"],
            "overlapAnalysis": overlap_analysis,
        }

    projector = projector_for(
        [section["geometry"] for section in sections]
        + [segment["geometry"] for segment in saved_segments]
    )
    for section in sections:
        section["projected"] = [
            projector.project(coordinate) for coordinate in section["geometry"]
        ]
    saved_by_id = {segment["id"]: segment for segment in saved_segments}
    for segment in saved_segments:
        segment["projected"] = [
            projector.project(coordinate) for coordinate in segment["geometry"]
        ]

    existing_nodes: dict[int, dict[str, Any]] = {}
    display_junctions: list[dict[str, Any]] = []
    displayed_node_ids: set[str | int] = set()
    new_nodes: list[dict[str, Any]] = []
    endpoint_nodes: dict[tuple[float, float], dict[str, Any]] = {}

    def existing_node(
        junction_id: int,
        coordinate: Sequence[float],
        *,
        display: bool = False,
    ) -> dict[str, Any]:
        if junction_id not in existing_nodes:
            existing_nodes[junction_id] = {
                "id": junction_id,
                "existingJunctionId": junction_id,
                "coordinate": _node_coordinate(coordinate),
            }
        node = existing_nodes[junction_id]
        if display and node["id"] not in displayed_node_ids:
            display_junctions.append(
                {
                    "id": node["id"],
                    "existingJunctionId": junction_id,
                    "longitude": node["coordinate"][0],
                    "latitude": node["coordinate"][1],
                    "elevation": (
                        node["coordinate"][2] if len(node["coordinate"]) == 3 else None
                    ),
                    "state": "added",
                }
            )
            displayed_node_ids.add(node["id"])
        return node

    def new_node(
        coordinate: Sequence[float],
        *,
        endpoint_key: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        if endpoint_key is not None and endpoint_key in endpoint_nodes:
            return endpoint_nodes[endpoint_key]
        node = {
            "id": f"draft-junction-{len(new_nodes) + 1}",
            "coordinate": _node_coordinate(coordinate),
        }
        new_nodes.append(node)
        if endpoint_key is not None:
            endpoint_nodes[endpoint_key] = node
        display_junctions.append(
            {
                "id": node["id"],
                "longitude": node["coordinate"][0],
                "latitude": node["coordinate"][1],
                "elevation": node["coordinate"][2] if len(node["coordinate"]) == 3 else None,
                "state": "added",
            }
        )
        displayed_node_ids.add(node["id"])
        return node

    saved_junction_points = [
        (junction, projector.project([junction["longitude"], junction["latitude"]]))
        for junction in saved_junctions
    ]

    def closest_existing_node(
        point: Sequence[float],
    ) -> tuple[dict[str, Any], tuple[float, float]] | None:
        candidates = [
            (distance(point, projected), junction, projected)
            for junction, projected in saved_junction_points
            if distance(point, projected) <= INTERSECTION_TOLERANCE_METRES
        ]
        if not candidates:
            return None
        _separation, junction, projected = min(candidates, key=lambda candidate: candidate[0])
        coordinate = [junction["longitude"], junction["latitude"]]
        if junction["elevation"] is not None:
            coordinate.append(junction["elevation"])
        return existing_node(junction["id"], coordinate, display=True), projected

    section_cuts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    saved_cuts: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for section in sections:
        geometry = section["geometry"]
        for position, coordinate in ((0.0, geometry[0]), (len(geometry) - 1.0, geometry[-1])):
            projected = projector.project(coordinate)
            existing = closest_existing_node(projected)
            if existing:
                node, _projected = existing
                cut_coordinate = node["coordinate"]
            else:
                key = (float(coordinate[0]), float(coordinate[1]))
                node = new_node(coordinate, endpoint_key=key)
                cut_coordinate = coordinate
            section_cuts[section["id"]].append(
                {"position": position, "coordinate": cut_coordinate, "node": node}
            )

    events: list[dict[str, Any]] = []

    def add_event(
        point: Sequence[float],
        participants: list[dict[str, Any]],
    ) -> None:
        existing = closest_existing_node(point)
        events.append(
            {
                "point": tuple(existing[1] if existing else point),
                "existingNode": existing[0] if existing else None,
                "participants": participants,
            }
        )

    # A trace passing close to an existing junction connects to that exact junction.
    for section in sections:
        best_by_junction: dict[int, tuple[float, int, float]] = {}
        for junction, junction_point in saved_junction_points:
            for leg_index, (start, end) in enumerate(
                zip(section["projected"], section["projected"][1:])
            ):
                fraction, _projected, separation = project_point_to_leg(
                    junction_point, start, end
                )
                if separation > INTERSECTION_TOLERANCE_METRES:
                    continue
                candidate = (separation, leg_index, fraction)
                current = best_by_junction.get(junction["id"])
                if current is None or candidate < current:
                    best_by_junction[junction["id"]] = candidate
        for junction_id, (_separation, leg_index, fraction) in best_by_junction.items():
            junction = next(
                item for item, _point in saved_junction_points if item["id"] == junction_id
            )
            point = projector.project([junction["longitude"], junction["latitude"]])
            add_event(
                point,
                [
                    {
                        "kind": "section",
                        "id": section["id"],
                        "position": position_on_geometry(leg_index, fraction),
                    }
                ],
            )

    # Intersections and endpoint-to-interior connections with saved geometry.
    for section in sections:
        for segment in saved_segments:
            for section_leg_index, (section_start, section_end) in enumerate(
                zip(section["projected"], section["projected"][1:])
            ):
                for saved_leg_index, (saved_start, saved_end) in enumerate(
                    zip(segment["projected"], segment["projected"][1:])
                ):
                    crossing = leg_intersection(
                        section_start,
                        section_end,
                        saved_start,
                        saved_end,
                    )
                    if crossing:
                        section_fraction, saved_fraction, point = crossing
                        add_event(
                            point,
                            [
                                {
                                    "kind": "section",
                                    "id": section["id"],
                                    "position": position_on_geometry(
                                        section_leg_index, section_fraction
                                    ),
                                },
                                {
                                    "kind": "saved",
                                    "id": segment["id"],
                                    "position": position_on_geometry(
                                        saved_leg_index, saved_fraction
                                    ),
                                },
                            ],
                        )

            endpoint_positions = (0.0, len(section["geometry"]) - 1.0)
            for endpoint_position in endpoint_positions:
                endpoint = section["projected"][int(endpoint_position)]
                best_connection: tuple[float, int, float, tuple[float, float]] | None = None
                for saved_leg_index, (saved_start, saved_end) in enumerate(
                    zip(segment["projected"], segment["projected"][1:])
                ):
                    fraction, projected, separation = project_point_to_leg(
                        endpoint, saved_start, saved_end
                    )
                    if (
                        separation <= INTERSECTION_TOLERANCE_METRES
                        and INTERSECTION_EPSILON < fraction < 1 - INTERSECTION_EPSILON
                    ):
                        candidate = (separation, saved_leg_index, fraction, projected)
                        if best_connection is None or candidate[:3] < best_connection[:3]:
                            best_connection = candidate
                if best_connection:
                    _separation, saved_leg_index, saved_fraction, point = best_connection
                    add_event(
                        point,
                        [
                            {
                                "kind": "section",
                                "id": section["id"],
                                "position": endpoint_position,
                            },
                            {
                                "kind": "saved",
                                "id": segment["id"],
                                "position": position_on_geometry(
                                    saved_leg_index, saved_fraction
                                ),
                            },
                        ],
                    )

    # Self-crossings and crossings between separate uploaded sections.
    trace_legs: list[dict[str, Any]] = []
    for section in sections:
        last_leg_index = len(section["projected"]) - 2
        closed = section["geometry"][0][:2] == section["geometry"][-1][:2]
        for leg_index, (start, end) in enumerate(
            zip(section["projected"], section["projected"][1:])
        ):
            trace_legs.append(
                {
                    "sectionId": section["id"],
                    "legIndex": leg_index,
                    "lastLegIndex": last_leg_index,
                    "closed": closed,
                    "start": start,
                    "end": end,
                }
            )

    for first_index, first_leg in enumerate(trace_legs):
        for second_leg in trace_legs[first_index + 1 :]:
            if first_leg["sectionId"] == second_leg["sectionId"]:
                separation = abs(first_leg["legIndex"] - second_leg["legIndex"])
                closes_loop = (
                    first_leg["closed"]
                    and {first_leg["legIndex"], second_leg["legIndex"]}
                    == {0, first_leg["lastLegIndex"]}
                )
                if separation <= 1 or closes_loop:
                    continue
            crossing = leg_intersection(
                first_leg["start"],
                first_leg["end"],
                second_leg["start"],
                second_leg["end"],
            )
            if crossing:
                first_fraction, second_fraction, point = crossing
                add_event(
                    point,
                    [
                        {
                            "kind": "section",
                            "id": first_leg["sectionId"],
                            "position": position_on_geometry(
                                first_leg["legIndex"], first_fraction
                            ),
                        },
                        {
                            "kind": "section",
                            "id": second_leg["sectionId"],
                            "position": position_on_geometry(
                                second_leg["legIndex"], second_fraction
                            ),
                        },
                    ],
                )

    # Existing-junction events consolidate by ID. New crossings within ten metres
    # form one junction so noisy traces do not create a knot of near-identical nodes.
    clusters: list[list[dict[str, Any]]] = []
    existing_clusters: dict[str, list[dict[str, Any]]] = {}
    unclustered_events: list[dict[str, Any]] = []
    for event in events:
        if event["existingNode"]:
            existing_clusters.setdefault(event["existingNode"]["id"], []).append(event)
        else:
            unclustered_events.append(event)
    while unclustered_events:
        cluster = [unclustered_events.pop(0)]
        changed = True
        while changed:
            changed = False
            for event in list(unclustered_events):
                if any(
                    distance(event["point"], member["point"])
                    <= INTERSECTION_TOLERANCE_METRES
                    for member in cluster
                ):
                    cluster.append(event)
                    unclustered_events.remove(event)
                    changed = True
        clusters.append(cluster)
    clusters.extend(existing_clusters.values())

    for cluster in clusters:
        existing = next(
            (event["existingNode"] for event in cluster if event["existingNode"]),
            None,
        )
        if existing:
            node = existing
            canonical_coordinate = node["coordinate"]
        else:
            point = (
                sum(event["point"][0] for event in cluster) / len(cluster),
                sum(event["point"][1] for event in cluster) / len(cluster),
            )
            canonical_coordinate = projector.unproject(point)
            section_participant = next(
                (
                    participant
                    for event in cluster
                    for participant in event["participants"]
                    if participant["kind"] == "section"
                ),
                None,
            )
            if section_participant:
                section = next(
                    item for item in sections if item["id"] == section_participant["id"]
                )
                source_coordinate = coordinate_at_position(
                    section["geometry"], section_participant["position"]
                )
                if len(source_coordinate) == 3:
                    canonical_coordinate.append(source_coordinate[2])
            node = new_node(canonical_coordinate)

        participants: dict[tuple[str, int], list[float]] = defaultdict(list)
        for event in cluster:
            for participant in event["participants"]:
                participants[(participant["kind"], participant["id"])].append(
                    participant["position"]
                )
        for (kind, identifier), positions in participants.items():
            if kind == "section":
                section = next(item for item in sections if item["id"] == identifier)
                canonical_projected = projector.project(canonical_coordinate)
                endpoint_positions = (
                    (0.0, section["projected"][0]),
                    (
                        len(section["geometry"]) - 1.0,
                        section["projected"][-1],
                    ),
                )
                positions = [
                    next(
                        (
                            endpoint_position
                            for endpoint_position, endpoint_point in endpoint_positions
                            if distance(canonical_projected, endpoint_point)
                            <= INTERSECTION_TOLERANCE_METRES
                        ),
                        position,
                    )
                    for position in positions
                ]
            position_groups: list[list[float]] = []
            for position in sorted(positions):
                if position_groups and abs(position - position_groups[-1][-1]) <= 1e-3:
                    position_groups[-1].append(position)
                else:
                    position_groups.append([position])
            for position_group in position_groups:
                cut = {
                    "position": sum(position_group) / len(position_group),
                    "coordinate": canonical_coordinate,
                    "node": node,
                }
                if kind == "section":
                    section_cuts[identifier].append(cut)
                else:
                    saved_cuts[identifier].append(cut)

    replacement_segments: list[dict[str, Any]] = []
    replaced_path_segment_ids: list[int] = []
    for saved_segment_id in sorted(saved_cuts):
        segment = saved_by_id[saved_segment_id]
        interior_cuts = [
            cut
            for cut in saved_cuts[saved_segment_id]
            if 1e-9 < cut["position"] < len(segment["geometry"]) - 1 - 1e-9
        ]
        if not interior_cuts:
            continue
        start_node = existing_node(
            segment["start_junction_id"],
            segment["geometry"][0],
        )
        end_node = existing_node(
            segment["end_junction_id"],
            segment["geometry"][-1],
        )
        cuts = [
            {"position": 0.0, "coordinate": segment["geometry"][0], "node": start_node},
            *interior_cuts,
            {
                "position": len(segment["geometry"]) - 1,
                "coordinate": segment["geometry"][-1],
                "node": end_node,
            },
        ]
        pieces = split_geometry(segment["geometry"], cuts)
        if len(pieces) <= 1:
            continue
        replaced_path_segment_ids.append(saved_segment_id)
        for piece in pieces:
            replacement_segments.append(
                _path_segment(
                    f"draft-replacement-{len(replacement_segments) + 1}",
                    piece,
                    source_filename=segment["source_filename"],
                    origin="replacement",
                    replaces_path_segment_id=saved_segment_id,
                    metadata=segment["metadata"],
                )
            )

    imported_segments: list[dict[str, Any]] = []
    draft_geometry_keys = {geometry_key(segment["geometry"]) for segment in replacement_segments}
    unchanged_saved_keys = {
        geometry_key(segment["geometry"])
        for segment in saved_segments
        if segment["id"] not in replaced_path_segment_ids
    }
    for section in sections:
        pieces = split_geometry(section["geometry"], section_cuts[section["id"]])
        for piece in pieces:
            key = geometry_key(piece["geometry"])
            if key in unchanged_saved_keys or key in draft_geometry_keys:
                continue
            imported_segments.append(
                _path_segment(
                    f"draft-path-segment-{len(imported_segments) + 1}",
                    piece,
                    source_filename=parsed_gpx["filename"],
                    origin="import",
                    metadata={},
                )
            )
            draft_geometry_keys.add(key)

    referenced_junction_ids = {
        segment[junction_field]
        for segment in replacement_segments + imported_segments
        for junction_field in ("startJunctionId", "endJunctionId")
    }
    display_junctions = [
        junction
        for junction in display_junctions
        if junction["id"] in referenced_junction_ids
    ]

    return {
        "name": parsed_gpx["name"],
        "filename": parsed_gpx["filename"],
        "junctions": display_junctions,
        "pathSegments": replacement_segments + imported_segments,
        "replacedPathSegmentIds": replaced_path_segment_ids,
        "skippedDuplicates": skipped_duplicates,
        "stats": parsed_gpx["stats"],
        "overlapAnalysis": overlap_analysis,
    }
