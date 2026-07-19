from __future__ import annotations

import math
from copy import deepcopy
from typing import Any
from uuid import uuid4

from flask import Flask

from path_network.geometry import (
    distance,
    geometry_key,
    project_point_to_geometry,
    projector_for,
    split_geometry,
)
from path_network.gpx import haversine_distance
from path_network.import_draft import build_import_draft
from path_network.repository import (
    RepositoryConflict,
    RepositoryError,
    commit_staged_network,
    get_path_network,
    get_path_network_revision,
)
from path_network.repository import SEGMENT_DIRECTION_MODES
from path_network.bus_repository import direction_display_name, get_bus_snapshot, TRAVERSALS


AREA_DUPLICATE_MAX_SEPARATION_METRES = 60.0
AREA_DUPLICATE_MAX_P90_SEPARATION_METRES = 25.0
AREA_DUPLICATE_MAX_MEDIAN_SEPARATION_METRES = 12.0
AREA_DUPLICATE_MAX_ENDPOINT_SEPARATION_METRES = 35.0
AREA_DUPLICATE_MIN_LENGTH_RATIO = 0.75
AREA_DUPLICATE_MAX_LENGTH_RATIO = 1.35
AREA_STUB_MAX_DISTANCE_METRES = 35
JUNCTION_PLACE_TYPES = {
    "",
    "end_of_route",
    "route_terminus",
}


class EditSessionError(ValueError):
    pass


class EditSessionConflict(EditSessionError):
    pass


class EditSessionValidation(EditSessionError):
    pass


JUNCTION_SNAP_TOLERANCE_METRES = 10.0
JUNCTION_MOVE_MINIMUM_SEPARATION_METRES = 1.0


def _session_store(app: Flask) -> dict[str, dict[str, Any]]:
    return app.extensions["edit_sessions"]


def _session(app: Flask, token: str) -> dict[str, Any]:
    session = _session_store(app).get(token)
    if session is None:
        raise EditSessionError("Edit session not found.")
    return session


def _saved_junction(junction: dict[str, Any]) -> dict[str, Any]:
    return {**deepcopy(junction), "state": "saved"}


def _junction_metadata(junction: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(junction.get("metadata") or {})


def _junction_is_protected(junction: dict[str, Any] | None) -> bool:
    return bool((junction or {}).get("metadata", {}).get("protected"))


def _protected_junction_ids(junctions: list[dict[str, Any]]) -> set[str]:
    return {
        str(junction["id"])
        for junction in junctions
        if junction["state"] in {"saved", "added"} and _junction_is_protected(junction)
    }


def _saved_path_segment(
    segment: dict[str, Any],
    replaced_ids: set[int],
) -> dict[str, Any]:
    return {
        **deepcopy(segment),
        "state": "replaced" if segment["id"] in replaced_ids else "saved",
    }


def _junction_id(segment: dict[str, Any], end: str) -> str | int:
    return segment.get(
        f"{end}JunctionId",
        segment.get(f"{end}_junction_id"),
    )


def _junction_coordinates(junction: dict[str, Any]) -> list[float]:
    coordinate = [float(junction["longitude"]), float(junction["latitude"])]
    if junction.get("elevation") is not None:
        coordinate.append(float(junction["elevation"]))
    return coordinate


def _added_path_segment(
    segment_id: str,
    source: dict[str, Any],
    piece: dict[str, Any],
) -> dict[str, Any]:
    geometry = deepcopy(piece["geometry"])
    return {
        "id": segment_id,
        "startJunctionId": piece["startNode"]["id"],
        "endJunctionId": piece["endNode"]["id"],
        "geometry": geometry,
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": source.get(
            "source_filename",
            source.get("sourceFilename"),
        ),
        "metadata": deepcopy(source.get("metadata") or {}),
        "directionMode": source.get("directionMode", source.get("direction_mode", "bidirectional")),
        "sourcePathSegmentId": source.get("sourcePathSegmentId", source.get("id")),
        "origin": "manual_split",
        "state": "added",
    }


def _active_incident_segments(
    path_segments: list[dict[str, Any]],
    junction_id: str | int,
) -> list[dict[str, Any]]:
    return [
        segment
        for segment in path_segments
        if segment["state"] in {"saved", "added"}
        and junction_id
        in {
            _junction_id(segment, "start"),
            _junction_id(segment, "end"),
        }
    ]


def _other_junction_id(
    segment: dict[str, Any],
    junction_id: str | int,
) -> str | int:
    start_id = _junction_id(segment, "start")
    end_id = _junction_id(segment, "end")
    if start_id == end_id:
        raise EditSessionValidation("Loop path segments cannot be merged.")
    if start_id == junction_id:
        return end_id
    if end_id == junction_id:
        return start_id
    raise EditSessionValidation(
        "A source path segment is not incident to the selected junction."
    )


def _geometry_toward_junction(
    segment: dict[str, Any],
    junction_id: str | int,
) -> list[list[float]]:
    geometry = deepcopy(segment["geometry"])
    return (
        geometry
        if _junction_id(segment, "end") == junction_id
        else list(reversed(geometry))
    )


def _geometry_away_from_junction(
    segment: dict[str, Any],
    junction_id: str | int,
) -> list[list[float]]:
    geometry = deepcopy(segment["geometry"])
    return (
        geometry
        if _junction_id(segment, "start") == junction_id
        else list(reversed(geometry))
    )


def _merged_path_segment(
    segment_id: str,
    junction_id: str | int,
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    start_junction_id = _other_junction_id(first, junction_id)
    end_junction_id = _other_junction_id(second, junction_id)
    if start_junction_id == end_junction_id:
        raise EditSessionValidation(
            "Removing this junction would create a loop, which is not supported."
        )
    first_metadata = first.get("metadata") or {}

    first_geometry = _geometry_toward_junction(first, junction_id)
    second_geometry = _geometry_away_from_junction(second, junction_id)
    if first_geometry[-1][:2] != second_geometry[0][:2]:
        raise EditSessionValidation(
            "The two path-segment geometries do not meet at the selected junction."
        )
    geometry = first_geometry + second_geometry[1:]
    first_source = first.get("source_filename", first.get("sourceFilename"))
    second_source = second.get("source_filename", second.get("sourceFilename"))
    return {
        "id": segment_id,
        "startJunctionId": start_junction_id,
        "endJunctionId": end_junction_id,
        "geometry": geometry,
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": (
            first_source if first_source == second_source else None
        ),
        "metadata": deepcopy(first_metadata),
        "origin": "junction_merge",
        "state": "added",
    }


def _moved_path_segment(
    segment_id: str,
    source: dict[str, Any],
    junction_id: str | int,
    replacement_junction_id: str,
    coordinate: list[float],
) -> dict[str, Any]:
    geometry = deepcopy(source["geometry"])
    start_id = _junction_id(source, "start")
    end_id = _junction_id(source, "end")
    replacement_coordinate = deepcopy(coordinate)
    if start_id == junction_id:
        start_id = replacement_junction_id
        geometry[0] = replacement_coordinate
    if end_id == junction_id:
        end_id = replacement_junction_id
        geometry[-1] = replacement_coordinate
    if len({tuple(point[:2]) for point in geometry}) < 2:
        raise EditSessionValidation(
            "Moving the junction there would collapse an attached path segment."
        )
    return {
        "id": segment_id,
        "startJunctionId": start_id,
        "endJunctionId": end_id,
        "geometry": geometry,
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": source.get(
            "source_filename",
            source.get("sourceFilename"),
        ),
        "metadata": deepcopy(source.get("metadata") or {}),
        "directionMode": source.get("directionMode", source.get("direction_mode", "bidirectional")),
        "sourcePathSegmentId": source.get("sourcePathSegmentId", source.get("id")),
        "origin": "junction_move",
        "state": "added",
    }


def _junction_merge_path_segment(
    segment_id: str,
    source: dict[str, Any],
    source_junction_id: str | int,
    target_junction: dict[str, Any],
) -> dict[str, Any]:
    target_coordinate = _junction_coordinates(target_junction)
    return _moved_path_segment(
        segment_id,
        source,
        source_junction_id,
        target_junction["id"],
        target_coordinate,
    )


def _rewired_path_segment(
    segment_id: str,
    source: dict[str, Any],
    endpoint_replacements: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    geometry = deepcopy(source["geometry"])
    start_id = _junction_id(source, "start")
    end_id = _junction_id(source, "end")
    if str(start_id) in endpoint_replacements:
        replacement = endpoint_replacements[str(start_id)]
        start_id = replacement["junctionId"]
        geometry[0] = deepcopy(replacement["coordinate"])
    if str(end_id) in endpoint_replacements:
        replacement = endpoint_replacements[str(end_id)]
        end_id = replacement["junctionId"]
        geometry[-1] = deepcopy(replacement["coordinate"])
    if str(start_id) == str(end_id) or len({tuple(point[:2]) for point in geometry}) < 2:
        raise EditSessionValidation(
            "Cleanup would collapse an externally connected path segment."
        )
    return {
        "id": segment_id,
        "startJunctionId": start_id,
        "endJunctionId": end_id,
        "geometry": geometry,
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": source.get(
            "source_filename",
            source.get("sourceFilename"),
        ),
        "metadata": deepcopy(source.get("metadata") or {}),
        "directionMode": source.get("directionMode", source.get("direction_mode", "bidirectional")),
        "sourcePathSegmentId": source.get("sourcePathSegmentId", source.get("id")),
        "origin": "duplicate_cleanup_rewire",
        "state": "added",
    }


def _edited_path_segment(
    segment_id: str,
    source: dict[str, Any],
    geometry: list[list[float]],
) -> dict[str, Any]:
    return {
        "id": segment_id,
        "startJunctionId": _junction_id(source, "start"),
        "endJunctionId": _junction_id(source, "end"),
        "geometry": deepcopy(geometry),
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": source.get(
            "source_filename",
            source.get("sourceFilename"),
        ),
        "metadata": deepcopy(source.get("metadata") or {}),
        "directionMode": source.get("directionMode", source.get("direction_mode", "bidirectional")),
        "sourcePathSegmentId": source.get("sourcePathSegmentId", source.get("id")),
        "origin": "geometry_edit",
        "state": "added",
    }


def _metadata_edited_path_segment(
    segment_id: str,
    source: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    replacement = _edited_path_segment(
        segment_id,
        source,
        deepcopy(source["geometry"]),
    )
    replacement["metadata"] = deepcopy(metadata)
    replacement["origin"] = "metadata_edit"
    return replacement


def _created_path_segment(
    segment_id: str,
    start_junction: dict[str, Any],
    end_junction: dict[str, Any],
    geometry: list[list[float]],
) -> dict[str, Any]:
    return {
        "id": segment_id,
        "startJunctionId": start_junction["id"],
        "endJunctionId": end_junction["id"],
        "geometry": deepcopy(geometry),
        "distance_m": round(
            sum(
                haversine_distance(previous, current)
                for previous, current in zip(geometry, geometry[1:])
            )
        ),
        "source_filename": None,
        "metadata": {},
        "origin": "manual_create",
        "state": "added",
    }


def _created_junction(junction_id: str, coordinate: list[float]) -> dict[str, Any]:
    return {
        "id": junction_id,
        "longitude": coordinate[0],
        "latitude": coordinate[1],
        "elevation": coordinate[2] if len(coordinate) == 3 else None,
        "metadata": {},
        "state": "added",
    }


def _normalize_coordinate(coordinate: Any, message: str) -> list[float]:
    if not isinstance(coordinate, list | tuple) or len(coordinate) not in {2, 3}:
        raise EditSessionValidation(message)
    try:
        return [float(value) for value in coordinate]
    except (TypeError, ValueError) as error:
        raise EditSessionValidation(message) from error


def _normalize_created_path_geometry(
    start_junction: dict[str, Any],
    end_junction: dict[str, Any],
    geometry: Any,
) -> list[list[float]]:
    if not isinstance(geometry, list) or len(geometry) < 2:
        raise EditSessionValidation("New path geometry needs at least two points.")
    normalized: list[list[float]] = []
    for coordinate in geometry:
        normalized.append(
            _normalize_coordinate(
                coordinate,
                "New path geometry contains an invalid point.",
            )
        )
    if normalized[0][:2] != _junction_coordinates(start_junction)[:2]:
        raise EditSessionValidation("New path geometry must start at the start junction.")
    if normalized[-1][:2] != _junction_coordinates(end_junction)[:2]:
        raise EditSessionValidation("New path geometry must end at the end junction.")
    if len({tuple(point[:2]) for point in normalized}) < 2:
        raise EditSessionValidation("New path geometry needs two distinct points.")
    return normalized


def _cleanup_endpoint_mapping(
    retained: dict[str, Any],
    removed: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    projector = projector_for([retained["geometry"], removed["geometry"]])
    retained_start = projector.project(retained["geometry"][0])
    retained_end = projector.project(retained["geometry"][-1])
    removed_start = projector.project(removed["geometry"][0])
    removed_end = projector.project(removed["geometry"][-1])
    same_direction = (
        distance(removed_start, retained_start)
        + distance(removed_end, retained_end)
    )
    reverse_direction = (
        distance(removed_start, retained_end)
        + distance(removed_end, retained_start)
    )
    retained_start_id = _junction_id(retained, "start")
    retained_end_id = _junction_id(retained, "end")
    removed_start_id = _junction_id(removed, "start")
    removed_end_id = _junction_id(removed, "end")
    if same_direction <= reverse_direction:
        return {
            str(removed_start_id): {
                "junctionId": retained_start_id,
                "coordinate": deepcopy(retained["geometry"][0]),
                "retainedEnd": "start",
            },
            str(removed_end_id): {
                "junctionId": retained_end_id,
                "coordinate": deepcopy(retained["geometry"][-1]),
                "retainedEnd": "end",
            },
        }
    return {
        str(removed_start_id): {
            "junctionId": retained_end_id,
            "coordinate": deepcopy(retained["geometry"][-1]),
            "retainedEnd": "end",
        },
        str(removed_end_id): {
            "junctionId": retained_start_id,
            "coordinate": deepcopy(retained["geometry"][0]),
            "retainedEnd": "start",
        },
    }


def _cleanup_traversal_direction(
    retained: dict[str, Any],
    removed: dict[str, Any],
) -> str:
    projector = projector_for([retained["geometry"], removed["geometry"]])
    retained_start = projector.project(retained["geometry"][0])
    retained_end = projector.project(retained["geometry"][-1])
    removed_start = projector.project(removed["geometry"][0])
    removed_end = projector.project(removed["geometry"][-1])
    same_direction = (
        distance(removed_start, retained_start)
        + distance(removed_end, retained_end)
    )
    reverse_direction = (
        distance(removed_start, retained_end)
        + distance(removed_end, retained_start)
    )
    return "same" if same_direction <= reverse_direction else "reverse"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(percentile * len(ordered)) - 1),
    )
    return ordered[index]


def _duplicate_cleanup_diagnostics(
    retained: dict[str, Any],
    removed: dict[str, Any],
    path_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    if str(retained["id"]) == str(removed["id"]):
        raise EditSessionValidation("Choose two different path segments.")
    if retained["state"] != "saved" or removed["state"] != "saved":
        raise EditSessionValidation(
            "Duplicate cleanup is available only for saved path segments."
        )
    projector = projector_for([retained["geometry"], removed["geometry"]])
    retained_projected = [projector.project(point) for point in retained["geometry"]]
    removed_projected = [projector.project(point) for point in removed["geometry"]]
    retained_length = sum(
        distance(start, end)
        for start, end in zip(retained_projected, retained_projected[1:])
    )
    removed_length = sum(
        distance(start, end)
        for start, end in zip(removed_projected, removed_projected[1:])
    )
    separations = [
        project_point_to_geometry(point, retained["geometry"])[2]
        for point in removed["geometry"]
    ] + [
        project_point_to_geometry(point, removed["geometry"])[2]
        for point in retained["geometry"]
    ]
    maximum_separation = max(separations) if separations else 0.0
    median_separation = _percentile(separations, 0.5)
    p90_separation = _percentile(separations, 0.9)
    retained_start = projector.project(retained["geometry"][0])
    retained_end = projector.project(retained["geometry"][-1])
    removed_start = projector.project(removed["geometry"][0])
    removed_end = projector.project(removed["geometry"][-1])
    endpoint_separation = min(
        max(
            distance(removed_start, retained_start),
            distance(removed_end, retained_end),
        ),
        max(
            distance(removed_start, retained_end),
            distance(removed_end, retained_start),
        ),
    )
    endpoint_mapping = _cleanup_endpoint_mapping(retained, removed)
    removed_endpoint_ids = {
        _junction_id(removed, "start"),
        _junction_id(removed, "end"),
    }
    external_segments = [
        segment
        for segment in path_segments
        if segment["state"] in {"saved", "added"}
        and str(segment["id"]) not in {str(retained["id"]), str(removed["id"])}
        and removed_endpoint_ids
        & {
            _junction_id(segment, "start"),
            _junction_id(segment, "end"),
        }
    ]
    metadata_conflict = (retained.get("metadata") or {}) != (removed.get("metadata") or {})
    return {
        "retainedPathSegmentId": retained["id"],
        "removedPathSegmentId": removed["id"],
        "retainedDistanceMetres": round(retained_length),
        "removedDistanceMetres": round(removed_length),
        "lengthRatio": round(
            removed_length / retained_length if retained_length else 0.0,
            4,
        ),
        "traversalDirection": _cleanup_traversal_direction(retained, removed),
        "maximumSeparationMetres": round(maximum_separation, 2),
        "medianSeparationMetres": round(median_separation, 2),
        "p90SeparationMetres": round(p90_separation, 2),
        "endpointSeparationMetres": round(endpoint_separation, 2),
        "metadataConflict": metadata_conflict,
        "retainedMetadata": deepcopy(retained.get("metadata") or {}),
        "removedMetadata": deepcopy(removed.get("metadata") or {}),
        "externalConnectionCount": len(external_segments),
        "endpointMapping": {
            removed_id: {
                "retainedJunctionId": replacement["junctionId"],
                "retainedEnd": replacement["retainedEnd"],
            }
            for removed_id, replacement in endpoint_mapping.items()
        },
    }


def _segment_bounds(segment: dict[str, Any]) -> tuple[float, float, float, float]:
    if "bounds_min_lon" in segment:
        return (
            float(segment["bounds_min_lon"]),
            float(segment["bounds_min_lat"]),
            float(segment["bounds_max_lon"]),
            float(segment["bounds_max_lat"]),
        )
    longitudes = [float(coordinate[0]) for coordinate in segment["geometry"]]
    latitudes = [float(coordinate[1]) for coordinate in segment["geometry"]]
    return (min(longitudes), min(latitudes), max(longitudes), max(latitudes))


def _bounds_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return (
        first[2] >= second[0]
        and first[0] <= second[2]
        and first[3] >= second[1]
        and first[1] <= second[3]
    )


def _coordinate_in_bounds(
    longitude: float,
    latitude: float,
    bounds: tuple[float, float, float, float],
) -> bool:
    return (
        bounds[0] <= float(longitude) <= bounds[2]
        and bounds[1] <= float(latitude) <= bounds[3]
    )


def _stage_duplicate_cleanup_operation(
    session: dict[str, Any],
    retained: dict[str, Any],
    removed: dict[str, Any],
    segments: list[dict[str, Any]],
    protected_junction_ids: set[str] | None = None,
) -> dict[str, Any]:
    diagnostics = _duplicate_cleanup_diagnostics(retained, removed, segments)
    removed_endpoint_ids = {
        _junction_id(removed, "start"),
        _junction_id(removed, "end"),
    }
    if {str(identifier) for identifier in removed_endpoint_ids} & (protected_junction_ids or set()):
        raise EditSessionValidation("Protected junctions cannot be cleaned up or merged.")
    external_segments = [
        segment
        for segment in segments
        if segment["state"] in {"saved", "added"}
        and str(segment["id"])
        not in {str(retained["id"]), str(removed["id"])}
        and removed_endpoint_ids
        & {
            _junction_id(segment, "start"),
            _junction_id(segment, "end"),
        }
    ]
    operation_id = uuid4().hex
    operation = {
        "type": "duplicate_cleanup",
        "retainedPathSegmentId": retained["id"],
        "removedPathSegmentId": removed["id"],
        "externalPathSegmentIds": [segment["id"] for segment in external_segments],
        "replacementPathSegmentIds": [
            f"duplicate-cleanup-{operation_id}-{index}"
            for index, _segment in enumerate(external_segments, start=1)
        ],
        "diagnostics": diagnostics,
    }
    session["operations"].append(operation)
    return operation


def _merge_at_junction_operation(
    junction: dict[str, Any],
    path_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    if _junction_is_protected(junction):
        raise EditSessionValidation("Protected junctions cannot be cleaned up or merged.")
    incident = _active_incident_segments(path_segments, junction["id"])
    if len(incident) != 2:
        raise EditSessionValidation(
            "A junction can be removed only when its staged degree is exactly two."
        )
    operation_id = uuid4().hex
    operation = {
        "type": "merge_at_junction",
        "junctionId": junction["id"],
        "pathSegmentIds": [segment["id"] for segment in incident],
        "mergedPathSegmentId": f"merged-path-segment-{operation_id}",
    }
    _merged_path_segment(
        operation["mergedPathSegmentId"],
        junction["id"],
        incident[0],
        incident[1],
    )
    return operation


def _stage_merge_at_junction_operation(
    session: dict[str, Any],
    junction: dict[str, Any],
    path_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    operation = _merge_at_junction_operation(junction, path_segments)
    session["operations"].append(operation)
    return operation


def _stage_delete_path_segment_operation(
    session: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    operation = {
        "type": "delete_path_segment",
        "pathSegmentId": segment["id"],
    }
    session["operations"].append(operation)
    return operation


def _is_high_confidence_duplicate(diagnostics: dict[str, Any]) -> bool:
    return (
        diagnostics["maximumSeparationMetres"] <= AREA_DUPLICATE_MAX_SEPARATION_METRES
        and diagnostics["p90SeparationMetres"] <= AREA_DUPLICATE_MAX_P90_SEPARATION_METRES
        and diagnostics["medianSeparationMetres"]
        <= AREA_DUPLICATE_MAX_MEDIAN_SEPARATION_METRES
        and diagnostics["endpointSeparationMetres"]
        <= AREA_DUPLICATE_MAX_ENDPOINT_SEPARATION_METRES
        and AREA_DUPLICATE_MIN_LENGTH_RATIO
        <= diagnostics["lengthRatio"]
        <= AREA_DUPLICATE_MAX_LENGTH_RATIO
    )


def _apply_split_operation(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    target = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["pathSegmentId"])
            and segment["state"] in {"saved", "added"}
        ),
        None,
    )
    if target is None:
        raise EditSessionValidation(
            "The selected path segment is no longer available to split."
        )

    start_id = _junction_id(target, "start")
    end_id = _junction_id(target, "end")
    start_node = {"id": start_id}
    end_node = {"id": end_id}
    split_node = {"id": operation["junctionId"]}
    pieces = split_geometry(
        target["geometry"],
        [
            {
                "position": 0.0,
                "coordinate": target["geometry"][0],
                "node": start_node,
            },
            {
                "position": operation["position"],
                "coordinate": operation["coordinate"],
                "node": split_node,
            },
            {
                "position": len(target["geometry"]) - 1,
                "coordinate": target["geometry"][-1],
                "node": end_node,
            },
        ],
    )
    if len(pieces) != 2:
        raise EditSessionValidation(
            "The selected position cannot split this path segment."
        )

    if target["state"] == "saved":
        target["state"] = "replaced"
    else:
        path_segments.remove(target)

    coordinate = operation["coordinate"]
    junctions.append(
        {
            "id": operation["junctionId"],
            "longitude": coordinate[0],
            "latitude": coordinate[1],
            "elevation": coordinate[2] if len(coordinate) == 3 else None,
            "metadata": {},
            "state": "added",
        }
    )
    path_segments.extend(
        _added_path_segment(segment_id, target, piece)
        for segment_id, piece in zip(operation["replacementPathSegmentIds"], pieces)
    )


def _apply_delete_operation(
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> set[str | int]:
    target = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["pathSegmentId"])
            and segment["state"] in {"saved", "added"}
        ),
        None,
    )
    if target is None:
        raise EditSessionValidation(
            "The selected path segment is no longer available to delete."
        )

    endpoint_ids = {
        _junction_id(target, "start"),
        _junction_id(target, "end"),
    }
    if target["state"] == "saved":
        target["state"] = "deleted"
    else:
        path_segments.remove(target)
    return endpoint_ids


def _apply_edit_geometry_operation(
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    target = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["pathSegmentId"])
            and segment["state"] in {"saved", "added"}
        ),
        None,
    )
    if target is None:
        raise EditSessionValidation("The selected path segment is no longer available.")
    replacement = _edited_path_segment(
        operation["replacementPathSegmentId"],
        target,
        operation["geometry"],
    )
    if target["state"] == "saved":
        target["state"] = "replaced"
    else:
        path_segments.remove(target)
    path_segments.append(replacement)


def _apply_edit_metadata_operation(
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    target = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["pathSegmentId"])
            and segment["state"] in {"saved", "added"}
        ),
        None,
    )
    if target is None:
        raise EditSessionValidation("The selected path segment is no longer available.")
    replacement = _metadata_edited_path_segment(
        operation["replacementPathSegmentId"],
        target,
        operation["metadata"],
    )
    if target["state"] == "saved":
        target["state"] = "replaced"
    else:
        path_segments.remove(target)
    path_segments.append(replacement)


def _apply_edit_junction_metadata_operation(
    junctions: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    target = next(
        (
            junction
            for junction in junctions
            if str(junction["id"]) == str(operation["junctionId"])
            and junction["state"] in {"saved", "added"}
        ),
        None,
    )
    if target is None:
        raise EditSessionValidation("The selected junction is no longer available.")
    target["metadata"] = deepcopy(operation["metadata"])


def _apply_create_path_segment_operation(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    start_junction = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(operation["startJunctionId"])
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if start_junction is None and operation.get("startCoordinate") is not None:
        start_junction = _created_junction(
            operation["startJunctionId"],
            _normalize_coordinate(operation["startCoordinate"], "New path start contains an invalid point."),
        )
        junctions.append(start_junction)
    end_junction = None
    if operation.get("targetPathSegmentId") is not None:
        target = next(
            (
                segment
                for segment in path_segments
                if str(segment["id"]) == str(operation["targetPathSegmentId"])
                and segment["state"] in {"saved", "added"}
            ),
            None,
        )
        if target is None:
            raise EditSessionValidation(
                "The selected target path is no longer available to split."
            )
        end_coordinate = _normalize_coordinate(
            operation["endCoordinate"],
            "New path endpoint contains an invalid point.",
        )
        end_junction = _created_junction(
            operation["endJunctionId"],
            end_coordinate,
        )
        start_id = _junction_id(target, "start")
        end_id = _junction_id(target, "end")
        pieces = split_geometry(
            target["geometry"],
            [
                {
                    "position": 0.0,
                    "coordinate": target["geometry"][0],
                    "node": {"id": start_id},
                },
                {
                    "position": operation["targetSplitPosition"],
                    "coordinate": end_coordinate,
                    "node": {"id": end_junction["id"]},
                },
                {
                    "position": len(target["geometry"]) - 1,
                    "coordinate": target["geometry"][-1],
                    "node": {"id": end_id},
                },
            ],
        )
        if len(pieces) != 2:
            raise EditSessionValidation(
                "The selected position cannot split the target path."
            )
        if target["state"] == "saved":
            target["state"] = "replaced"
        else:
            path_segments.remove(target)
        junctions.append(end_junction)
        path_segments.extend(
            _added_path_segment(segment_id, target, piece)
            for segment_id, piece in zip(operation["replacementPathSegmentIds"], pieces)
        )
    elif operation.get("endCoordinate") is not None:
        end_coordinate = _normalize_coordinate(
            operation["endCoordinate"],
            "New path endpoint contains an invalid point.",
        )
        end_junction = _created_junction(
            operation["endJunctionId"],
            end_coordinate,
        )
        junctions.append(end_junction)
    elif operation.get("endJunctionId") is not None:
        end_junction = next(
            (
                candidate
                for candidate in junctions
                if str(candidate["id"]) == str(operation["endJunctionId"])
                and candidate["state"] in {"saved", "added"}
            ),
            None,
        )
    if start_junction is None or end_junction is None:
        raise EditSessionValidation("New path endpoints are no longer available.")
    geometry = _normalize_created_path_geometry(
        start_junction,
        end_junction,
        operation["geometry"],
    )
    path_segments.append(
        _created_path_segment(
            operation["pathSegmentId"],
            start_junction,
            end_junction,
            geometry,
        )
    )


def _apply_merge_operation(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    junction = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(operation["junctionId"])
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if junction is None:
        raise EditSessionValidation(
            "The selected junction is no longer available to merge."
        )
    if _junction_is_protected(junction):
        raise EditSessionValidation("Protected junctions cannot be cleaned up or merged.")

    incident = _active_incident_segments(path_segments, junction["id"])
    expected_ids = {str(identifier) for identifier in operation["pathSegmentIds"]}
    if (
        len(incident) != 2
        or {str(segment["id"]) for segment in incident} != expected_ids
    ):
        raise EditSessionValidation(
            "The selected junction no longer has exactly two mergeable path segments."
        )

    merged = _merged_path_segment(
        operation["mergedPathSegmentId"],
        junction["id"],
        incident[0],
        incident[1],
    )
    for segment in incident:
        if segment["state"] == "saved":
            segment["state"] = "replaced"
        else:
            path_segments.remove(segment)
    if junction["state"] == "saved":
        junction["state"] = "deleted"
    else:
        junctions.remove(junction)
    path_segments.append(merged)


def _apply_move_operation(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    junction = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(operation["junctionId"])
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if junction is None:
        raise EditSessionValidation(
            "The selected junction is no longer available to move."
        )

    incident = _active_incident_segments(path_segments, junction["id"])
    expected_ids = {
        str(identifier) for identifier in operation["pathSegmentIds"]
    }
    if {str(segment["id"]) for segment in incident} != expected_ids:
        raise EditSessionValidation(
            "The paths attached to the selected junction have changed."
        )

    replacement_ids = {
        str(source_id): replacement_id
        for source_id, replacement_id in zip(
            operation["pathSegmentIds"],
            operation["replacementPathSegmentIds"],
        )
    }
    replacements = [
        _moved_path_segment(
            replacement_ids[str(segment["id"])],
            segment,
            junction["id"],
            operation["replacementJunctionId"],
            operation["coordinate"],
        )
        for segment in incident
    ]
    target_replacements: list[dict[str, Any]] = []
    target_id = operation.get("targetPathSegmentId")
    target = None
    if target_id is not None:
        target = next(
            (
                segment
                for segment in path_segments
                if str(segment["id"]) == str(target_id)
                and segment["state"] in {"saved", "added"}
            ),
            None,
        )
        if target is None:
            raise EditSessionValidation(
                "The target path segment is no longer available."
            )
        if target in incident:
            raise EditSessionValidation(
                "Choose a different path segment to create a new connection."
            )
        pieces = split_geometry(
            target["geometry"],
            [
                {
                    "position": 0.0,
                    "coordinate": target["geometry"][0],
                    "node": {"id": _junction_id(target, "start")},
                },
                {
                    "position": operation["targetPosition"],
                    "coordinate": operation["coordinate"],
                    "node": {"id": operation["replacementJunctionId"]},
                },
                {
                    "position": len(target["geometry"]) - 1,
                    "coordinate": target["geometry"][-1],
                    "node": {"id": _junction_id(target, "end")},
                },
            ],
        )
        if len(pieces) != 2:
            raise EditSessionValidation(
                "The selected position cannot split the target path segment."
            )
        target_replacements = [
            {
                **_added_path_segment(segment_id, target, piece),
                "origin": "junction_move_split",
            }
            for segment_id, piece in zip(
                operation["targetReplacementPathSegmentIds"],
                pieces,
            )
        ]
    for segment in incident:
        if segment["state"] == "saved":
            segment["state"] = "replaced"
        else:
            path_segments.remove(segment)
    if target is not None:
        if target["state"] == "saved":
            target["state"] = "replaced"
        else:
            path_segments.remove(target)
    if junction["state"] == "saved":
        junction["state"] = "deleted"
    else:
        junctions.remove(junction)
    coordinate = operation["coordinate"]
    junctions.append(
        {
            "id": operation["replacementJunctionId"],
            "longitude": coordinate[0],
            "latitude": coordinate[1],
            "elevation": coordinate[2] if len(coordinate) == 3 else None,
            "metadata": _junction_metadata(junction),
            "state": "added",
        }
    )
    path_segments.extend(replacements + target_replacements)


def _apply_merge_junctions_operation(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> set[str | int]:
    source = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(operation["sourceJunctionId"])
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    target = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(operation["targetJunctionId"])
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if source is None or target is None:
        raise EditSessionValidation(
            "The selected junctions are no longer available to merge."
        )
    if str(source["id"]) == str(target["id"]):
        raise EditSessionValidation("Choose two different junctions to merge.")
    if _junction_is_protected(source):
        raise EditSessionValidation("Protected junctions cannot be cleaned up or merged.")

    incident = _active_incident_segments(path_segments, source["id"])
    expected_ids = {
        str(identifier) for identifier in operation["pathSegmentIds"]
    }
    if {str(segment["id"]) for segment in incident} != expected_ids:
        raise EditSessionValidation(
            "The paths attached to the source junction have changed."
        )
    direct_segments = []
    rewired_segments = []
    for segment in incident:
        endpoint_ids = {
            str(_junction_id(segment, "start")),
            str(_junction_id(segment, "end")),
        }
        if str(target["id"]) in endpoint_ids:
            direct_segments.append(segment)
        else:
            rewired_segments.append(segment)

    replacements = [
        _junction_merge_path_segment(
            replacement_id,
            segment,
            source["id"],
            target,
        )
        for segment, replacement_id in zip(
            rewired_segments,
            operation["replacementPathSegmentIds"],
        )
    ]
    for segment in incident:
        if segment in direct_segments:
            if segment["state"] == "saved":
                segment["state"] = "deleted"
            else:
                path_segments.remove(segment)
        elif segment["state"] == "saved":
            segment["state"] = "replaced"
        else:
            path_segments.remove(segment)
    if source["state"] == "saved":
        source["state"] = "deleted"
    else:
        junctions.remove(source)
    path_segments.extend(replacements)
    return {source["id"]}


def _apply_duplicate_cleanup_operation(
    path_segments: list[dict[str, Any]],
    operation: dict[str, Any],
) -> set[str | int]:
    retained = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["retainedPathSegmentId"])
            and segment["state"] == "saved"
        ),
        None,
    )
    removed = next(
        (
            segment
            for segment in path_segments
            if str(segment["id"]) == str(operation["removedPathSegmentId"])
            and segment["state"] == "saved"
        ),
        None,
    )
    if retained is None or removed is None:
        raise EditSessionValidation(
            "The duplicate cleanup path segments are no longer available."
        )
    _duplicate_cleanup_diagnostics(retained, removed, path_segments)
    endpoint_replacements = _cleanup_endpoint_mapping(retained, removed)
    removed_endpoint_ids = {
        _junction_id(removed, "start"),
        _junction_id(removed, "end"),
    }
    external_segments = [
        segment
        for segment in path_segments
        if segment["state"] in {"saved", "added"}
        and str(segment["id"])
        not in {str(retained["id"]), str(removed["id"])}
        and removed_endpoint_ids
        & {
            _junction_id(segment, "start"),
            _junction_id(segment, "end"),
        }
    ]
    if len(external_segments) != len(operation["externalPathSegmentIds"]):
        raise EditSessionValidation(
            "External connections for this duplicate cleanup have changed."
        )
    expected_external_ids = {
        str(identifier) for identifier in operation["externalPathSegmentIds"]
    }
    if {str(segment["id"]) for segment in external_segments} != expected_external_ids:
        raise EditSessionValidation(
            "External connections for this duplicate cleanup have changed."
        )

    replacements = []
    collapsed_segments = []
    for segment_id, segment in zip(
        operation["replacementPathSegmentIds"],
        external_segments,
    ):
        try:
            replacements.append(
                _rewired_path_segment(segment_id, segment, endpoint_replacements)
            )
        except EditSessionValidation as error:
            if str(error) != "Cleanup would collapse an externally connected path segment.":
                raise
            collapsed_segments.append(segment)
    removed["state"] = "deleted"
    for segment in external_segments:
        if segment["state"] == "saved":
            segment["state"] = "deleted" if segment in collapsed_segments else "replaced"
        else:
            path_segments.remove(segment)
    path_segments.extend(replacements)
    collapsed_endpoint_ids = {
        _junction_id(segment, end)
        for segment in collapsed_segments
        for end in ("start", "end")
    }
    return removed_endpoint_ids | collapsed_endpoint_ids


def _derive_orphan_junctions(
    junctions: list[dict[str, Any]],
    path_segments: list[dict[str, Any]],
    orphan_candidates: set[str | int],
) -> None:
    active_junction_ids = {
        _junction_id(segment, end)
        for segment in path_segments
        if segment["state"] in {"saved", "added"}
        for end in ("start", "end")
    }
    for junction in list(junctions):
        if junction["id"] in active_junction_ids:
            continue
        if junction.get("preserveOrphan"):
            continue
        if _junction_is_protected(junction):
            continue
        if junction["state"] == "added":
            junctions.remove(junction)
        elif junction["id"] in orphan_candidates:
            junction["state"] = "deleted"


def _deduplicate_added_path_segments(
    path_segments: list[dict[str, Any]],
    saved_network: dict[str, Any],
) -> tuple[set[str | int], list[dict[str, Any]]]:
    allowed_counts: dict[tuple[tuple[float, ...], ...], int] = {}
    for segment in saved_network["pathSegments"]:
        key = geometry_key(segment["geometry"])
        allowed_counts[key] = max(allowed_counts.get(key, 0) + 1, 1)

    active_counts: dict[tuple[tuple[float, ...], ...], int] = {}
    for segment in path_segments:
        if segment["state"] in {"saved", "added"}:
            key = geometry_key(segment["geometry"])
            active_counts[key] = active_counts.get(key, 0) + 1

    removed_endpoint_ids: set[str | int] = set()
    skipped_duplicates: list[dict[str, Any]] = []

    for segment in reversed(path_segments):
        if segment["state"] != "added":
            continue
        key = geometry_key(segment["geometry"])
        allowed_count = max(allowed_counts.get(key, 0), 1)
        if active_counts.get(key, 0) <= allowed_count:
            continue
        path_segments.remove(segment)
        active_counts[key] -= 1
        removed_endpoint_ids.update(
            {
                _junction_id(segment, "start"),
                _junction_id(segment, "end"),
            }
        )
        skipped_duplicates.append(
            {
                "pathSegmentId": segment["id"],
                "reason": "Duplicate staged geometry",
            }
        )

    return removed_endpoint_ids, skipped_duplicates


def _derive(session: dict[str, Any]) -> dict[str, Any]:
    saved_network = session["savedNetwork"]
    import_result = None
    for operation in session["operations"]:
        if operation["type"] == "import_trace":
            import_result = build_import_draft(
                operation["parsedGpx"],
                saved_network=saved_network,
                overlap_decisions=operation.get("overlapDecisions"),
                overlap_adjustments=operation.get("overlapAdjustments"),
            )

    replaced_ids = set(
        import_result["replacedPathSegmentIds"] if import_result else []
    )
    junctions = [_saved_junction(junction) for junction in saved_network["junctions"]]
    path_segments = [
        _saved_path_segment(segment, replaced_ids)
        for segment in saved_network["pathSegments"]
    ]

    added_junctions: list[dict[str, Any]] = []
    added_segments: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    import_summary = None
    if import_result:
        added_junctions = [
            deepcopy(junction)
            for junction in import_result["junctions"]
            if "existingJunctionId" not in junction
        ]
        added_segments = deepcopy(import_result["pathSegments"])
        skipped_duplicates = deepcopy(import_result["skippedDuplicates"])
        import_summary = {
            "name": import_result["name"],
            "filename": import_result["filename"],
            "stats": deepcopy(import_result["stats"]),
            "overlapAnalysis": deepcopy(import_result["overlapAnalysis"]),
        }
        junctions.extend(added_junctions)
        path_segments.extend(added_segments)

    orphan_candidates: set[str | int] = set()
    for operation in session["operations"]:
        if operation["type"] == "split_path_segment":
            _apply_split_operation(junctions, path_segments, operation)
        elif operation["type"] == "delete_path_segment":
            orphan_candidates.update(
                _apply_delete_operation(path_segments, operation)
            )
            for merge_operation in operation.get("autoMergeJunctions", []):
                _apply_merge_operation(junctions, path_segments, merge_operation)
        elif operation["type"] == "edit_path_geometry":
            _apply_edit_geometry_operation(path_segments, operation)
        elif operation["type"] == "edit_path_metadata":
            _apply_edit_metadata_operation(path_segments, operation)
        elif operation["type"] == "set_segment_direction":
            target = next((item for item in path_segments if str(item["id"]) == str(operation["pathSegmentId"]) and item["state"] in {"saved", "added"}), None)
            if target is None:
                raise EditSessionValidation("The selected segment does not exist.")
            replacement = _edited_path_segment(operation["replacementPathSegmentId"], target, target["geometry"])
            replacement["directionMode"] = operation["directionMode"]
            if target["state"] == "saved":
                target["state"] = "replaced"
            else:
                path_segments.remove(target)
            path_segments.append(replacement)
        elif operation["type"] == "edit_junction_metadata":
            _apply_edit_junction_metadata_operation(junctions, operation)
        elif operation["type"] == "add_junction":
            junction = _created_junction(operation["junctionId"], operation["coordinate"])
            junction["origin"] = "manual_create"
            junction["preserveOrphan"] = True
            junctions.append(junction)
        elif operation["type"] == "create_path_segment":
            _apply_create_path_segment_operation(junctions, path_segments, operation)
        elif operation["type"] == "merge_at_junction":
            _apply_merge_operation(junctions, path_segments, operation)
        elif operation["type"] == "move_junction":
            _apply_move_operation(junctions, path_segments, operation)
        elif operation["type"] == "merge_junctions":
            orphan_candidates.update(
                _apply_merge_junctions_operation(junctions, path_segments, operation)
            )
        elif operation["type"] == "duplicate_cleanup":
            orphan_candidates.update(
                _apply_duplicate_cleanup_operation(path_segments, operation)
            )

    removed_duplicate_endpoints, staged_duplicates = _deduplicate_added_path_segments(
        path_segments,
        saved_network,
    )
    orphan_candidates.update(removed_duplicate_endpoints)
    skipped_duplicates.extend(staged_duplicates)

    _derive_orphan_junctions(junctions, path_segments, orphan_candidates)

    saved_bus = session.get("savedBus", {"routes": [], "routeMemberships": []})
    routes = []
    route_directions = []
    for route in saved_bus["routes"]:
        routes.append({key: deepcopy(value) for key, value in route.items() if key != "directions"} | {"state": "saved"})
        route_directions.extend({**deepcopy(direction), "state": "saved"} for direction in route["directions"])
    route_memberships = [{**deepcopy(item), "state": "saved"} for item in saved_bus["routeMemberships"]]
    for operation in session["operations"]:
        if operation["type"] == "create_bus_route":
            routes.append({**deepcopy(operation["route"]), "state": "added"})
        elif operation["type"] == "create_route_direction":
            route_directions.append({**deepcopy(operation["direction"]), "state": "added"})
        elif operation["type"] == "update_route_direction":
            direction = next(
                item for item in route_directions
                if str(item["id"]) == str(operation["routeDirectionId"])
            )
            direction.update(deepcopy(operation["changes"]))
            if direction["state"] == "saved":
                direction["state"] = "updated"
        elif operation["type"] == "assign_route_segment":
            route_memberships = [
                item for item in route_memberships
                if not (
                    str(item["routeDirectionId"]) == str(operation["routeDirectionId"])
                    and str(item["pathSegmentId"]) == str(operation["pathSegmentId"])
                )
            ]
            route_memberships.append({
                "id": operation["membershipId"],
                "routeDirectionId": operation["routeDirectionId"],
                "pathSegmentId": operation["pathSegmentId"],
                "traversal": operation["traversal"],
                "state": "added",
            })
        elif operation["type"] == "remove_route_segment":
            route_memberships = [
                item for item in route_memberships
                if not (
                    str(item["routeDirectionId"]) == str(operation["routeDirectionId"])
                    and str(item["pathSegmentId"]) == str(operation["pathSegmentId"])
                )
            ]
        elif operation["type"] == "create_path_segment" and operation.get("routeDirectionId") is not None:
            route_memberships.append({
                "id": f"route-membership-{operation['pathSegmentId']}",
                "routeDirectionId": operation["routeDirectionId"],
                "pathSegmentId": operation["pathSegmentId"],
                "traversal": operation.get("traversal", "both"),
                "state": "added",
            })

    active_segment_ids = {str(segment["id"]) for segment in path_segments if segment["state"] in {"saved", "added"}}
    replacements: dict[str, list[str | int]] = {}
    for segment in path_segments:
        if segment["state"] == "added" and segment.get("sourcePathSegmentId") is not None:
            replacements.setdefault(str(segment["sourcePathSegmentId"]), []).append(segment["id"])
    expanded_memberships = []
    for membership in route_memberships:
        segment_id = membership["pathSegmentId"]
        if str(segment_id) in active_segment_ids:
            expanded_memberships.append(membership)
        else:
            for replacement_id in replacements.get(str(segment_id), []):
                expanded_memberships.append({
                    **membership,
                    "id": f"inherited-{membership['id']}-{replacement_id}",
                    "pathSegmentId": replacement_id,
                    "state": "added",
                })
    route_memberships = expanded_memberships

    junction_by_id = {str(item["id"]): item for item in junctions if item["state"] in {"saved", "added"}}
    for direction in route_directions:
        start = junction_by_id.get(str(direction.get("startJunctionId")))
        end = junction_by_id.get(str(direction.get("endJunctionId")))
        if start and end:
            direction.update({
                "startLongitude": start["longitude"], "startLatitude": start["latitude"],
                "endLongitude": end["longitude"], "endLatitude": end["latitude"],
            })
        direction["displayName"] = direction_display_name(direction)

    operation_labels = {
        "split_path_segment": "Add junction to path",
        "delete_path_segment": "Delete path segment",
        "edit_path_geometry": "Edit path trace points",
        "create_path_segment": "Create path segment",
        "edit_path_metadata": "Edit path metadata",
        "set_segment_direction": "Set segment direction",
        "edit_junction_metadata": "Edit junction metadata",
        "add_junction": "Add junction",
        "merge_at_junction": "Remove junction and merge",
        "move_junction": "Move junction",
        "merge_junctions": "Merge junctions",
        "duplicate_cleanup": "Clean up duplicate path",
        "create_bus_route": "Create bus route",
        "create_route_direction": "Create route direction",
        "update_route_direction": "Update route direction",
        "assign_route_segment": "Assign route to segment",
        "remove_route_segment": "Remove route from segment",
    }
    operation_summaries = [
        {
            "position": index,
            "type": operation["type"],
            "label": (
                f"Import {operation['parsedGpx']['name']}"
                if operation["type"] == "import_trace"
                else operation_labels.get(operation["type"], "Edit network")
            ),
        }
        for index, operation in enumerate(session["operations"], start=1)
    ]
    change_summary = {
        "addedJunctions": sum(
            junction["state"] == "added" for junction in junctions
        ),
        "addedPathSegments": sum(
            segment["state"] == "added" for segment in path_segments
        ),
        "replacedPathSegments": sum(
            segment["state"] == "replaced" for segment in path_segments
        ),
        "deletedJunctions": sum(
            junction["state"] == "deleted" for junction in junctions
        ),
        "deletedPathSegments": sum(
            segment["state"] == "deleted" for segment in path_segments
        ),
        "operationCount": len(operation_summaries),
    }

    is_stale = get_path_network_revision() != session["baseRevision"]
    has_unresolved_overlaps = bool(
        import_summary
        and import_summary["overlapAnalysis"]["hasUnresolvedOverlaps"]
    )
    return {
        "token": session["token"],
        "baseRevision": session["baseRevision"],
        "isStale": is_stale,
        "canUndo": bool(session["operations"]),
        "canCommit": (
            bool(session["operations"])
            and not is_stale
            and not has_unresolved_overlaps
        ),
        "historyPosition": len(session["operations"]),
        "operations": operation_summaries,
        "changeSummary": change_summary,
        "network": {
            "junctions": junctions,
            "pathSegments": path_segments,
            "bounds": saved_network["bounds"],
        },
        "routes": routes,
        "routeDirections": route_directions,
        "routeMemberships": route_memberships,
        "import": import_summary,
        "skippedDuplicates": skipped_duplicates,
    }


def create_edit_session(app: Flask) -> dict[str, Any]:
    saved_network = get_path_network()
    token = uuid4().hex
    session = {
        "token": token,
        "baseRevision": get_path_network_revision(saved_network),
        "savedNetwork": deepcopy(saved_network),
        "savedBus": deepcopy(get_bus_snapshot()),
        "operations": [],
    }
    _session_store(app)[token] = session
    return _derive(session)


def get_edit_session(app: Flask, token: str) -> dict[str, Any]:
    return _derive(_session(app, token))


def add_junction(
    app: Flask,
    token: str,
    longitude: Any,
    latitude: Any,
) -> dict[str, Any]:
    session = _session(app, token)
    coordinate = _normalize_coordinate(
        [longitude, latitude],
        "The new junction contains an invalid coordinate.",
    )
    operation_id = uuid4().hex
    operation = {
        "type": "add_junction",
        "junctionId": f"created-junction-{operation_id}",
        "coordinate": coordinate,
    }
    session["operations"].append(operation)
    try:
        result = _derive(session)
    except Exception:
        session["operations"].pop()
        raise
    result["createdJunction"] = {"junctionId": operation["junctionId"]}
    return result


def import_trace(
    app: Flask,
    token: str,
    parsed_gpx: dict[str, Any],
) -> dict[str, Any]:
    session = _session(app, token)
    if any(operation["type"] == "import_trace" for operation in session["operations"]):
        raise EditSessionConflict(
            "This edit session already contains a GPX import. Undo or Cancel it first."
        )
    if session["operations"]:
        raise EditSessionConflict(
            "Undo or Cancel the staged manual changes before importing a GPX file."
        )
    session["operations"].append(
        {
            "type": "import_trace",
            "parsedGpx": deepcopy(parsed_gpx),
            "overlapDecisions": {},
            "overlapAdjustments": {},
        }
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def set_overlap_boundary_adjustment(
    app: Flask,
    token: str,
    candidate_key: str,
    boundary: str,
    longitude: float,
    latitude: float,
) -> dict[str, Any]:
    if boundary not in {"start", "end"}:
        raise EditSessionValidation("Overlap boundary must be start or end.")
    session = _session(app, token)
    if get_path_network_revision() != session["baseRevision"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )
    import_operation = _import_operation(session)
    _reviewable_overlap_candidate(session, candidate_key)

    adjustments = import_operation.setdefault("overlapAdjustments", {})
    previous = adjustments.get(candidate_key)
    current = [
        adjustment
        for adjustment in (previous or [])
        if adjustment.get("boundary") != boundary
    ]
    current.append(
        {
            "boundary": boundary,
            "longitude": float(longitude),
            "latitude": float(latitude),
        }
    )
    adjustments[candidate_key] = current
    try:
        return _derive(session)
    except Exception:
        if previous is None:
            adjustments.pop(candidate_key, None)
        else:
            adjustments[candidate_key] = previous
        raise


def reset_overlap_boundary_adjustment(
    app: Flask,
    token: str,
    candidate_key: str,
) -> dict[str, Any]:
    session = _session(app, token)
    if get_path_network_revision() != session["baseRevision"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )
    import_operation = _import_operation(session)
    _reviewable_overlap_candidate(session, candidate_key)
    adjustments = import_operation.setdefault("overlapAdjustments", {})
    previous = adjustments.pop(candidate_key, None)
    try:
        return _derive(session)
    except Exception:
        if previous is not None:
            adjustments[candidate_key] = previous
        raise


def _import_operation(session: dict[str, Any]) -> dict[str, Any]:
    import_operation = next(
        (
            operation
            for operation in session["operations"]
            if operation["type"] == "import_trace"
        ),
        None,
    )
    if import_operation is None:
        raise EditSessionValidation("This edit session has no GPX import.")
    return import_operation


def _reviewable_overlap_candidate(
    session: dict[str, Any],
    candidate_key: str,
) -> dict[str, Any]:
    derived = _derive(session)
    candidate = next(
        (
            item
            for item in derived["import"]["overlapAnalysis"]["candidates"]
            if item["key"] == candidate_key
            and item["reviewType"]
            in {"complete_section_reuse", "partial_section_reuse"}
        ),
        None,
    )
    if candidate is None:
        raise EditSessionValidation(
            "The overlap candidate is no longer available for review."
        )
    return candidate


def set_overlap_decision(
    app: Flask,
    token: str,
    candidate_key: str,
    decision: str | None,
) -> dict[str, Any]:
    if decision not in {None, "reuse", "keep"}:
        raise EditSessionValidation(
            "Overlap decision must be either reuse or keep."
        )
    session = _session(app, token)
    if get_path_network_revision() != session["baseRevision"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )
    import_operation = _import_operation(session)
    _reviewable_overlap_candidate(session, candidate_key)

    decisions = import_operation.setdefault("overlapDecisions", {})
    previous = decisions.get(candidate_key)
    if decision is None:
        decisions.pop(candidate_key, None)
    else:
        decisions[candidate_key] = decision
    try:
        return _derive(session)
    except Exception:
        if previous is None:
            decisions.pop(candidate_key, None)
        else:
            decisions[candidate_key] = previous
        raise


def split_path_segment(
    app: Flask,
    token: str,
    path_segment_id: str,
    longitude: float,
    latitude: float,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    segment = next(
        (
            candidate
            for candidate in derived["network"]["pathSegments"]
            if str(candidate["id"]) == str(path_segment_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if segment is None:
        raise EditSessionValidation("The selected path segment cannot be split.")

    try:
        clicked = [float(longitude), float(latitude)]
    except (TypeError, ValueError) as error:
        raise EditSessionValidation(
            "Split coordinates must be valid longitude and latitude numbers."
        ) from error

    position, coordinate, _separation = project_point_to_geometry(
        clicked,
        segment["geometry"],
    )
    endpoint_projector = projector_for([segment["geometry"]])
    projected_coordinate = endpoint_projector.project(coordinate)
    endpoint_distances = [
        distance(
            projected_coordinate,
            endpoint_projector.project(endpoint),
        )
        for endpoint in (segment["geometry"][0], segment["geometry"][-1])
    ]
    if min(endpoint_distances) <= JUNCTION_SNAP_TOLERANCE_METRES:
        endpoint_index = 0 if endpoint_distances[0] <= endpoint_distances[1] else 1
        end = "start" if endpoint_index == 0 else "end"
        result = derived
        result["selectedObject"] = {
            "type": "junction",
            "id": _junction_id(segment, end),
            "reason": "existing_endpoint",
        }
        return result

    operation_id = uuid4().hex
    session["operations"].append(
        {
            "type": "split_path_segment",
            "pathSegmentId": segment["id"],
            "position": position,
            "coordinate": coordinate,
            "junctionId": f"split-junction-{operation_id}",
            "replacementPathSegmentIds": [
                f"split-path-segment-{operation_id}-1",
                f"split-path-segment-{operation_id}-2",
            ],
        }
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def delete_path_segment(
    app: Flask,
    token: str,
    path_segment_id: str,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    segment = next(
        (
            candidate
            for candidate in derived["network"]["pathSegments"]
            if str(candidate["id"]) == str(path_segment_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if segment is None:
        raise EditSessionValidation("The selected path segment cannot be deleted.")

    endpoint_ids = [
        _junction_id(segment, "start"),
        _junction_id(segment, "end"),
    ]
    operation = {
        "type": "delete_path_segment",
        "pathSegmentId": segment["id"],
        "autoMergeJunctions": [],
    }
    session["operations"].append(operation)
    for endpoint_id in endpoint_ids:
        derived_after_delete = _derive(session)
        junction = next(
            (
                candidate
                for candidate in derived_after_delete["network"]["junctions"]
                if str(candidate["id"]) == str(endpoint_id)
                and candidate["state"] in {"saved", "added"}
            ),
            None,
        )
        if junction is None:
            continue
        if len(
            _active_incident_segments(
                derived_after_delete["network"]["pathSegments"],
                junction["id"],
            )
        ) != 2:
            continue
        try:
            merge_operation = _merge_at_junction_operation(
                junction,
                derived_after_delete["network"]["pathSegments"],
            )
        except EditSessionValidation:
            continue
        operation["autoMergeJunctions"].append(merge_operation)
        try:
            _derive(session)
        except EditSessionValidation:
            operation["autoMergeJunctions"].pop()
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def create_path_segment(
    app: Flask,
    token: str,
    start_junction_id: str | None,
    end_junction_id: str | None,
    geometry: Any,
    *,
    end_coordinate: Any = None,
    target_path_segment_id: str | None = None,
    start_coordinate: Any = None,
    route_direction_id: Any = None,
    traversal: str = "both",
) -> dict[str, Any]:
    session = _session(app, token)
    if end_junction_id is not None and end_coordinate is not None:
        raise EditSessionValidation(
            "Choose either an existing end junction or a new endpoint."
        )
    if target_path_segment_id is not None and end_coordinate is None:
        raise EditSessionValidation("Choose a point on the target path for the new endpoint.")
    if target_path_segment_id is not None and end_junction_id is not None:
        raise EditSessionValidation(
            "Choose either an existing end junction or a target path endpoint."
        )
    derived = _derive(session)
    if traversal not in TRAVERSALS:
        raise EditSessionValidation("Invalid route traversal.")
    if route_direction_id is not None:
        direction = next((item for item in derived["routeDirections"] if str(item["id"]) == str(route_direction_id)), None)
        if direction is None:
            raise EditSessionValidation("The selected route direction does not exist.")
        route_direction_id = direction["id"]
    junctions = derived["network"]["junctions"]
    target_segment = None
    split_target_path = False
    start_junction = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(start_junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    end_junction = next(
        (
            candidate
            for candidate in junctions
            if str(candidate["id"]) == str(end_junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if target_path_segment_id is not None:
        target_segment = next(
            (
                candidate
                for candidate in derived["network"]["pathSegments"]
                if str(candidate["id"]) == str(target_path_segment_id)
                and candidate["state"] in {"saved", "added"}
            ),
            None,
        )
        if target_segment is None:
            raise EditSessionValidation("The selected target path cannot receive the new path.")
    start_coordinate = _normalize_coordinate(start_coordinate, "New path start contains an invalid point.") if start_coordinate is not None else None
    if start_junction is None and start_coordinate is None:
        raise EditSessionValidation("Choose an existing start junction or a new start point.")
    if start_junction is None:
        start_junction = _created_junction(f"created-start-junction-{uuid4().hex}", start_coordinate)
    if end_junction is None:
        if end_coordinate is None:
            raise EditSessionValidation("Choose two existing junctions for the new path.")
        end_coordinate = _normalize_coordinate(
            end_coordinate,
            "New path endpoint contains an invalid point.",
        )
        operation_id = uuid4().hex
        if target_segment is not None:
            position, coordinate, _separation = project_point_to_geometry(
                end_coordinate,
                target_segment["geometry"],
            )
            endpoint_projector = projector_for([target_segment["geometry"]])
            projected_coordinate = endpoint_projector.project(coordinate)
            endpoint_distances = [
                distance(
                    projected_coordinate,
                    endpoint_projector.project(endpoint),
                )
                for endpoint in (target_segment["geometry"][0], target_segment["geometry"][-1])
            ]
            if min(endpoint_distances) <= JUNCTION_SNAP_TOLERANCE_METRES:
                endpoint_index = 0 if endpoint_distances[0] <= endpoint_distances[1] else 1
                end = "start" if endpoint_index == 0 else "end"
                end_junction = next(
                    (
                        candidate
                        for candidate in junctions
                        if str(candidate["id"]) == str(_junction_id(target_segment, end))
                        and candidate["state"] in {"saved", "added"}
                    ),
                    None,
                )
                if end_junction is None:
                    raise EditSessionValidation("The target path endpoint is no longer available.")
                end_coordinate = None
            else:
                end_junction = _created_junction(
                    f"split-junction-{operation_id}",
                    coordinate,
                )
                split_target_path = True
                end_coordinate = coordinate
        else:
            end_junction = _created_junction(
                f"created-junction-{operation_id}",
                end_coordinate,
            )
    else:
        operation_id = uuid4().hex
        if str(start_junction["id"]) == str(end_junction["id"]):
            raise EditSessionValidation("Choose two different junctions for the new path.")

    if str(start_junction["id"]) == str(end_junction["id"]):
        raise EditSessionValidation("Choose two different junctions for the new path.")
    if target_segment is not None:
        geometry = [*geometry[:-1], _junction_coordinates(end_junction)]
    normalized_geometry = _normalize_created_path_geometry(
        start_junction,
        end_junction,
        geometry,
    )
    duplicate_connections = []
    if end_coordinate is None:
        endpoint_ids = {str(start_junction["id"]), str(end_junction["id"])}
        duplicate_connections = [
            segment
            for segment in derived["network"]["pathSegments"]
            if segment["state"] in {"saved", "added"}
            and {
                str(_junction_id(segment, "start")),
                str(_junction_id(segment, "end")),
            }
            == endpoint_ids
        ]
    operation = {
        "type": "create_path_segment",
        "startJunctionId": start_junction["id"],
        "endJunctionId": end_junction["id"],
        "geometry": normalized_geometry,
        "pathSegmentId": f"created-path-segment-{operation_id}",
    }
    if start_coordinate is not None:
        operation["startCoordinate"] = start_coordinate
    if route_direction_id is not None:
        operation["routeDirectionId"] = route_direction_id
        operation["traversal"] = traversal
    if end_coordinate is not None:
        operation["endCoordinate"] = end_coordinate
    if split_target_path and target_segment is not None:
        operation["targetPathSegmentId"] = target_segment["id"]
        operation["targetSplitPosition"] = position
        operation["replacementPathSegmentIds"] = [
            f"split-path-segment-{operation_id}-1",
            f"split-path-segment-{operation_id}-2",
        ]
    session["operations"].append(operation)
    try:
        result = _derive(session)
    except Exception:
        session["operations"].pop()
        raise
    result["createdPathSegment"] = {
        "pathSegmentId": operation["pathSegmentId"],
        "startJunctionId": operation["startJunctionId"],
        "endJunctionId": operation["endJunctionId"],
        "createdEndJunction": end_coordinate is not None,
        "splitTargetPath": split_target_path,
        "duplicateConnectionCount": len(duplicate_connections),
    }
    return result


def edit_path_geometry(
    app: Flask,
    token: str,
    path_segment_id: str,
    geometry: list[Any],
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    segment = next(
        (
            candidate
            for candidate in derived["network"]["pathSegments"]
            if str(candidate["id"]) == str(path_segment_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if segment is None:
        raise EditSessionValidation("The selected path segment cannot be edited.")
    if not isinstance(geometry, list):
        raise EditSessionValidation("Path geometry must be a list of coordinates.")

    try:
        edited_geometry = [
            [float(coordinate[0]), float(coordinate[1])]
            + (
                [float(coordinate[2])]
                if len(coordinate) > 2 and coordinate[2] is not None
                else []
            )
            for coordinate in geometry
        ]
    except (TypeError, ValueError, IndexError) as error:
        raise EditSessionValidation(
            "Path geometry coordinates must be valid longitude and latitude numbers."
        ) from error

    if len(edited_geometry) < 2 or len({tuple(point[:2]) for point in edited_geometry}) < 2:
        raise EditSessionValidation("Path geometry needs at least two distinct points.")
    if edited_geometry[0][:2] != segment["geometry"][0][:2]:
        raise EditSessionValidation("Path geometry must keep its start junction point.")
    if edited_geometry[-1][:2] != segment["geometry"][-1][:2]:
        raise EditSessionValidation("Path geometry must keep its end junction point.")

    operation_id = uuid4().hex
    session["operations"].append(
        {
            "type": "edit_path_geometry",
            "pathSegmentId": segment["id"],
            "geometry": edited_geometry,
            "replacementPathSegmentId": f"edited-path-segment-{operation_id}",
        }
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def edit_path_metadata(
    app: Flask,
    token: str,
    path_segment_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    segment = next(
        (
            candidate
            for candidate in derived["network"]["pathSegments"]
            if str(candidate["id"]) == str(path_segment_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if segment is None:
        raise EditSessionValidation("The selected path segment cannot be edited.")
    if not isinstance(metadata, dict):
        raise EditSessionValidation("Path metadata must be an object.")

    preference = metadata.get("preference", "ok") or "ok"
    if preference not in {"hard_avoid", "avoid", "ok", "like", "destination"}:
        raise EditSessionValidation("Path preference is not valid.")

    route_flags = metadata.get("route_flags", [])
    if not isinstance(route_flags, list) or not all(
        isinstance(flag, str) and flag for flag in route_flags
    ):
        raise EditSessionValidation("Route flags must be a list of text values.")
    normalized_route_flags = [flag.strip() for flag in route_flags]
    if not all(normalized_route_flags):
        raise EditSessionValidation("Route flags must be a list of text values.")

    notes = metadata.get("notes", "")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise EditSessionValidation("Path notes must be text.")

    normalized_metadata = deepcopy(segment.get("metadata") or {})
    if preference == "ok":
        normalized_metadata.pop("preference", None)
    else:
        normalized_metadata["preference"] = preference
    normalized_metadata["route_flags"] = sorted(set(normalized_route_flags))
    normalized_metadata["notes"] = notes.strip()

    operation_id = uuid4().hex
    session["operations"].append(
        {
            "type": "edit_path_metadata",
            "pathSegmentId": segment["id"],
            "metadata": normalized_metadata,
            "replacementPathSegmentId": f"metadata-path-segment-{operation_id}",
        }
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def edit_junction_metadata(
    app: Flask,
    token: str,
    junction_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    junction = next(
        (
            candidate
            for candidate in derived["network"]["junctions"]
            if str(candidate["id"]) == str(junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if junction is None:
        raise EditSessionValidation("The selected junction cannot be edited.")
    if not isinstance(metadata, dict):
        raise EditSessionValidation("Junction metadata must be an object.")

    protected = metadata.get("protected", False)
    if not isinstance(protected, bool):
        raise EditSessionValidation("Protected must be true or false.")
    place_type = metadata.get("place_type", "")
    if place_type is None:
        place_type = ""
    if not isinstance(place_type, str) or place_type not in JUNCTION_PLACE_TYPES:
        raise EditSessionValidation("Junction place type is not valid.")
    name = metadata.get("name", "")
    if name is None:
        name = ""
    if not isinstance(name, str):
        raise EditSessionValidation("Junction name must be text.")
    notes = metadata.get("notes", "")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise EditSessionValidation("Junction notes must be text.")

    normalized_metadata = deepcopy(junction.get("metadata") or {})
    normalized_metadata["protected"] = protected
    normalized_metadata["place_type"] = place_type
    if place_type in {"end_of_route", "route_terminus"}:
        normalized_metadata["protected"] = True
    normalized_metadata["name"] = name.strip()
    normalized_metadata["notes"] = notes.strip()

    session["operations"].append(
        {
            "type": "edit_junction_metadata",
            "junctionId": junction["id"],
            "metadata": normalized_metadata,
        }
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def compare_duplicate_cleanup(
    app: Flask,
    token: str,
    first_path_segment_id: str,
    second_path_segment_id: str,
    *,
    retained_path_segment_id: str | None = None,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    segments = derived["network"]["pathSegments"]
    first = next(
        (
            segment
            for segment in segments
            if str(segment["id"]) == str(first_path_segment_id)
        ),
        None,
    )
    second = next(
        (
            segment
            for segment in segments
            if str(segment["id"]) == str(second_path_segment_id)
        ),
        None,
    )
    if first is None or second is None:
        raise EditSessionValidation("Choose two saved path segments to compare.")
    if retained_path_segment_id is None or str(retained_path_segment_id) == str(first["id"]):
        retained, removed = first, second
    elif str(retained_path_segment_id) == str(second["id"]):
        retained, removed = second, first
    else:
        raise EditSessionValidation("The retained path must be one of the compared paths.")
    return _duplicate_cleanup_diagnostics(retained, removed, segments)


def stage_duplicate_cleanup(
    app: Flask,
    token: str,
    retained_path_segment_id: str,
    removed_path_segment_id: str,
) -> dict[str, Any]:
    session = _session(app, token)
    if get_path_network_revision() != session["baseRevision"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )
    derived = _derive(session)
    segments = derived["network"]["pathSegments"]
    retained = next(
        (
            segment
            for segment in segments
            if str(segment["id"]) == str(retained_path_segment_id)
        ),
        None,
    )
    removed = next(
        (
            segment
            for segment in segments
            if str(segment["id"]) == str(removed_path_segment_id)
        ),
        None,
    )
    if retained is None or removed is None:
        raise EditSessionValidation("Choose two saved path segments to clean up.")
    _stage_duplicate_cleanup_operation(
        session,
        retained,
        removed,
        segments,
        _protected_junction_ids(derived["network"]["junctions"]),
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def stage_area_duplicate_cleanup(
    app: Flask,
    token: str,
    bounds: dict[str, Any],
) -> dict[str, Any]:
    session = _session(app, token)
    if get_path_network_revision() != session["baseRevision"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )

    try:
        area_bounds = (
            min(float(bounds["minLongitude"]), float(bounds["maxLongitude"])),
            min(float(bounds["minLatitude"]), float(bounds["maxLatitude"])),
            max(float(bounds["minLongitude"]), float(bounds["maxLongitude"])),
            max(float(bounds["minLatitude"]), float(bounds["maxLatitude"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EditSessionValidation(
            "Cleanup area bounds must be valid longitude and latitude numbers."
        ) from error

    duplicate_cleanup_count = 0
    stub_delete_count = 0
    junction_merge_count = 0
    skipped_count = 0
    original_operation_count = len(session["operations"])
    removed_ids: set[str] = set()
    skipped_duplicate_pairs: set[tuple[str, str]] = set()
    skipped_degree_two_junction_ids: set[str] = set()

    while True:
        derived = _derive(session)
        segments = derived["network"]["pathSegments"]
        candidates = [
            segment
            for segment in segments
            if segment["state"] == "saved"
            and str(segment["id"]) not in removed_ids
            and _bounds_intersect(_segment_bounds(segment), area_bounds)
        ]
        best_pair: tuple[float, str, dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
        for index, first in enumerate(candidates):
            for second in candidates[index + 1 :]:
                try:
                    first_keeps = int(first["id"]) <= int(second["id"])
                except (TypeError, ValueError):
                    first_keeps = str(first["id"]) <= str(second["id"])
                retained = first if first_keeps else second
                removed = second if first_keeps else first
                pair_key = tuple(sorted((str(retained["id"]), str(removed["id"]))))
                if pair_key in skipped_duplicate_pairs:
                    continue
                try:
                    diagnostics = _duplicate_cleanup_diagnostics(retained, removed, segments)
                except EditSessionValidation:
                    skipped_count += 1
                    skipped_duplicate_pairs.add(pair_key)
                    continue
                if not _is_high_confidence_duplicate(diagnostics):
                    skipped_count += 1
                    skipped_duplicate_pairs.add(pair_key)
                    continue
                score = (
                    float(diagnostics["maximumSeparationMetres"]),
                    str(retained["id"]),
                )
                if best_pair is None or score < best_pair[:2]:
                    best_pair = (*score, retained, removed, diagnostics)
        if best_pair is None:
            break
        _score, _retained_id, retained, removed, _diagnostics = best_pair
        operation_count_before_candidate = len(session["operations"])
        _stage_duplicate_cleanup_operation(
            session,
            retained,
            removed,
            segments,
            _protected_junction_ids(derived["network"]["junctions"]),
        )
        try:
            _derive(session)
        except EditSessionValidation:
            del session["operations"][operation_count_before_candidate:]
            skipped_count += 1
            skipped_duplicate_pairs.add(
                tuple(sorted((str(retained["id"]), str(removed["id"]))))
            )
            continue
        duplicate_cleanup_count += 1
        removed_ids.add(str(removed["id"]))

    while True:
        derived = _derive(session)
        protected_ids = _protected_junction_ids(derived["network"]["junctions"])
        active_segments = [
            segment
            for segment in derived["network"]["pathSegments"]
            if segment["state"] in {"saved", "added"}
        ]
        degrees: dict[str, int] = {}
        for segment in active_segments:
            for end in ("start", "end"):
                junction_id = str(_junction_id(segment, end))
                degrees[junction_id] = degrees.get(junction_id, 0) + 1
        candidate = next(
            (
                segment
                for segment in active_segments
                if int(segment["distance_m"]) <= AREA_STUB_MAX_DISTANCE_METRES
                and _bounds_intersect(_segment_bounds(segment), area_bounds)
                and str(_junction_id(segment, "start")) not in protected_ids
                and str(_junction_id(segment, "end")) not in protected_ids
                and sorted(
                    (
                        degrees.get(str(_junction_id(segment, "start")), 0),
                        degrees.get(str(_junction_id(segment, "end")), 0),
                    )
                )[0]
                == 1
                and sorted(
                    (
                        degrees.get(str(_junction_id(segment, "start")), 0),
                        degrees.get(str(_junction_id(segment, "end")), 0),
                    )
                )[1]
                >= 3
            ),
            None,
        )
        if candidate is None:
            break
        operation_count_before_candidate = len(session["operations"])
        _stage_delete_path_segment_operation(session, candidate)
        try:
            _derive(session)
        except EditSessionValidation:
            del session["operations"][operation_count_before_candidate:]
            skipped_count += 1
            break
        stub_delete_count += 1

    while True:
        derived = _derive(session)
        candidate = next(
            (
                junction
                for junction in derived["network"]["junctions"]
                if junction["state"] in {"saved", "added"}
                and not _junction_is_protected(junction)
                and str(junction["id"]) not in skipped_degree_two_junction_ids
                and _coordinate_in_bounds(
                    junction["longitude"],
                    junction["latitude"],
                    area_bounds,
                )
                and len(
                    _active_incident_segments(
                        derived["network"]["pathSegments"],
                        junction["id"],
                    )
                )
                == 2
            ),
            None,
        )
        if candidate is None:
            break
        operation_count_before_candidate = len(session["operations"])
        try:
            _stage_merge_at_junction_operation(
                session,
                candidate,
                derived["network"]["pathSegments"],
            )
            _derive(session)
        except EditSessionValidation:
            del session["operations"][operation_count_before_candidate:]
            skipped_count += 1
            skipped_degree_two_junction_ids.add(str(candidate["id"]))
            continue
        junction_merge_count += 1

    staged_count = duplicate_cleanup_count + stub_delete_count + junction_merge_count
    if staged_count == 0:
        raise EditSessionValidation(
            "No high-confidence duplicate paths, short stubs, or degree-two junctions were found in that area."
        )
    try:
        result = _derive(session)
    except Exception:
        del session["operations"][original_operation_count:]
        raise
    result["areaCleanup"] = {
        "stagedCount": staged_count,
        "duplicateCleanupCount": duplicate_cleanup_count,
        "stubDeleteCount": stub_delete_count,
        "junctionMergeCount": junction_merge_count,
        "skippedCount": skipped_count,
        "maximumSeparationMetres": AREA_DUPLICATE_MAX_SEPARATION_METRES,
        "minimumLengthRatio": AREA_DUPLICATE_MIN_LENGTH_RATIO,
        "maximumLengthRatio": AREA_DUPLICATE_MAX_LENGTH_RATIO,
        "maximumStubDistanceMetres": AREA_STUB_MAX_DISTANCE_METRES,
    }
    return result


def merge_at_junction(
    app: Flask,
    token: str,
    junction_id: str,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    junction = next(
        (
            candidate
            for candidate in derived["network"]["junctions"]
            if str(candidate["id"]) == str(junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if junction is None:
        raise EditSessionValidation("The selected junction cannot be removed.")

    _stage_merge_at_junction_operation(
        session,
        junction,
        derived["network"]["pathSegments"],
    )
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def merge_junctions(
    app: Flask,
    token: str,
    source_junction_id: str,
    target_junction_id: str,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    source = next(
        (
            candidate
            for candidate in derived["network"]["junctions"]
            if str(candidate["id"]) == str(source_junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    target = next(
        (
            candidate
            for candidate in derived["network"]["junctions"]
            if str(candidate["id"]) == str(target_junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if source is None or target is None:
        raise EditSessionValidation("Choose two active junctions to merge.")
    if str(source["id"]) == str(target["id"]):
        raise EditSessionValidation("Choose two different junctions to merge.")

    incident = _active_incident_segments(
        derived["network"]["pathSegments"],
        source["id"],
    )
    if not incident:
        raise EditSessionValidation("The source junction has no paths to merge.")
    rewired = [
        segment
        for segment in incident
        if str(target["id"])
        not in {
            str(_junction_id(segment, "start")),
            str(_junction_id(segment, "end")),
        }
    ]
    operation_id = uuid4().hex
    operation = {
        "type": "merge_junctions",
        "sourceJunctionId": source["id"],
        "targetJunctionId": target["id"],
        "pathSegmentIds": [segment["id"] for segment in incident],
        "replacementPathSegmentIds": [
            f"junction-merge-{operation_id}-{index}"
            for index, _segment in enumerate(rewired, start=1)
        ],
    }
    _apply_merge_junctions_operation(
        [deepcopy(junction) for junction in derived["network"]["junctions"]],
        [deepcopy(segment) for segment in derived["network"]["pathSegments"]],
        operation,
    )
    session["operations"].append(operation)
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def move_junction(
    app: Flask,
    token: str,
    junction_id: str,
    longitude: float,
    latitude: float,
    target_path_segment_id: str | None = None,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    junction = next(
        (
            candidate
            for candidate in derived["network"]["junctions"]
            if str(candidate["id"]) == str(junction_id)
            and candidate["state"] in {"saved", "added"}
        ),
        None,
    )
    if junction is None:
        raise EditSessionValidation("The selected junction cannot be moved.")
    try:
        clicked_coordinate = [float(longitude), float(latitude)]
    except (TypeError, ValueError) as error:
        raise EditSessionValidation(
            "Move coordinates must be valid longitude and latitude numbers."
        ) from error
    if not all(math.isfinite(value) for value in clicked_coordinate):
        raise EditSessionValidation(
            "Move coordinates must be finite longitude and latitude numbers."
        )
    target = None
    target_position = None
    if target_path_segment_id is not None:
        target = next(
            (
                candidate
                for candidate in derived["network"]["pathSegments"]
                if str(candidate["id"]) == str(target_path_segment_id)
                and candidate["state"] in {"saved", "added"}
            ),
            None,
        )
        if target is None:
            raise EditSessionValidation(
                "The selected target path segment cannot be split."
            )
        if junction["id"] in {
            _junction_id(target, "start"),
            _junction_id(target, "end"),
        }:
            raise EditSessionValidation(
                "Choose a path segment not already attached to this junction."
            )
        target_position, coordinate, _separation = project_point_to_geometry(
            clicked_coordinate,
            target["geometry"],
        )
        target_projector = projector_for([target["geometry"]])
        projected_coordinate = target_projector.project(coordinate)
        endpoint_distances = [
            distance(
                projected_coordinate,
                target_projector.project(endpoint),
            )
            for endpoint in (target["geometry"][0], target["geometry"][-1])
        ]
        if min(endpoint_distances) <= JUNCTION_SNAP_TOLERANCE_METRES:
            raise EditSessionValidation(
                "Choose an interior position more than ten metres from the target path endpoints."
            )
    else:
        coordinate = clicked_coordinate
    if len(coordinate) == 2 and junction.get("elevation") is not None:
        coordinate.append(float(junction["elevation"]))

    original_coordinate = [junction["longitude"], junction["latitude"]]
    if (
        haversine_distance(original_coordinate, coordinate)
        < JUNCTION_MOVE_MINIMUM_SEPARATION_METRES
    ):
        raise EditSessionValidation(
            "Choose a position at least one metre from the current junction."
        )
    for other in derived["network"]["junctions"]:
        if (
            other["state"] not in {"saved", "added"}
            or str(other["id"]) == str(junction["id"])
        ):
            continue
        if (
            haversine_distance(
                [other["longitude"], other["latitude"]],
                coordinate,
            )
            < JUNCTION_MOVE_MINIMUM_SEPARATION_METRES
        ):
            raise EditSessionValidation(
                "That position is too close to another junction."
            )

    incident = _active_incident_segments(
        derived["network"]["pathSegments"],
        junction["id"],
    )
    operation_id = uuid4().hex
    operation = {
        "type": "move_junction",
        "junctionId": junction["id"],
        "pathSegmentIds": [segment["id"] for segment in incident],
        "replacementJunctionId": f"moved-junction-{operation_id}",
        "replacementPathSegmentIds": [
            f"moved-path-segment-{operation_id}-{index}"
            for index in range(1, len(incident) + 1)
        ],
        "coordinate": coordinate,
    }
    if target is not None:
        operation.update(
            {
                "targetPathSegmentId": target["id"],
                "targetPosition": target_position,
                "targetReplacementPathSegmentIds": [
                    f"moved-target-path-segment-{operation_id}-1",
                    f"moved-target-path-segment-{operation_id}-2",
                ],
            }
        )
    for segment, replacement_id in zip(
        incident,
        operation["replacementPathSegmentIds"],
    ):
        _moved_path_segment(
            replacement_id,
            segment,
            junction["id"],
            operation["replacementJunctionId"],
            coordinate,
        )
    session["operations"].append(operation)
    try:
        return _derive(session)
    except Exception:
        session["operations"].pop()
        raise


def undo_edit_session(app: Flask, token: str) -> dict[str, Any]:
    session = _session(app, token)
    if not session["operations"]:
        raise EditSessionConflict("There is no staged operation to undo.")
    session["operations"].pop()
    return _derive(session)


def stage_create_bus_route(
    app: Flask,
    token: str,
    route_code: Any,
    display_name: Any = None,
    colour: Any = None,
) -> dict[str, Any]:
    session = _session(app, token)
    route_code = str(route_code or "").strip()
    if not route_code or len(route_code) > 20:
        raise EditSessionValidation("Route code is required and must be at most 20 characters.")
    derived = _derive(session)
    if any(route["routeCode"].lower() == route_code.lower() for route in derived["routes"]):
        raise EditSessionValidation("That route code already exists.")
    operation_id = uuid4().hex
    session["operations"].append({
        "type": "create_bus_route",
        "route": {
            "id": f"bus-route-{operation_id}",
            "routeCode": route_code,
            "displayName": str(display_name).strip() if display_name else None,
            "colour": colour,
        },
    })
    return _derive(session)


def stage_create_route_direction(
    app: Flask,
    token: str,
    bus_route_id: Any,
    start_junction_id: Any = None,
    end_junction_id: Any = None,
    custom_direction_name: Any = None,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    route = next((route for route in derived["routes"] if str(route["id"]) == str(bus_route_id)), None)
    if route is None:
        raise EditSessionValidation("The selected bus route does not exist.")
    bus_route_id = route["id"]
    existing_directions = [
        direction
        for direction in derived["routeDirections"]
        if str(direction["busRouteId"]) == str(bus_route_id)
        and direction.get("state") != "deleted"
    ]
    if len(existing_directions) >= 2:
        raise EditSessionValidation("A bus route can have at most two directions.")
    if (start_junction_id is None) != (end_junction_id is None):
        raise EditSessionValidation("Set both direction endpoints or leave both undefined.")
    active_nodes = {str(node["id"]) for node in derived["network"]["junctions"] if node["state"] in {"saved", "added"}}
    if start_junction_id is not None and (
        str(start_junction_id) not in active_nodes or str(end_junction_id) not in active_nodes
    ):
        raise EditSessionValidation("A route direction endpoint does not exist.")
    operation_id = uuid4().hex
    session["operations"].append({
        "type": "create_route_direction",
        "direction": {
            "id": f"route-direction-{operation_id}",
            "busRouteId": bus_route_id,
            "startJunctionId": start_junction_id,
            "endJunctionId": end_junction_id,
            "customDirectionName": str(custom_direction_name).strip() if custom_direction_name else None,
        },
    })
    return _derive(session)


def stage_update_route_direction(
    app: Flask,
    token: str,
    route_direction_id: Any,
    start_junction_id: Any,
    end_junction_id: Any,
    custom_direction_name: Any = None,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    direction = next((item for item in derived["routeDirections"] if str(item["id"]) == str(route_direction_id)), None)
    if direction is None:
        raise EditSessionValidation("The selected route direction does not exist.")
    if (start_junction_id is None) != (end_junction_id is None):
        raise EditSessionValidation("Set both direction endpoints or leave both undefined.")
    start_node = end_node = None
    if start_junction_id is not None:
        active_nodes = {str(node["id"]) for node in derived["network"]["junctions"] if node["state"] in {"saved", "added"}}
        if str(start_junction_id) not in active_nodes or str(end_junction_id) not in active_nodes:
            raise EditSessionValidation("A route direction endpoint does not exist.")
        if str(start_junction_id) == str(end_junction_id):
            raise EditSessionValidation("Direction endpoints must be different nodes.")
        start_node = next(node for node in derived["network"]["junctions"] if str(node["id"]) == str(start_junction_id))
        end_node = next(node for node in derived["network"]["junctions"] if str(node["id"]) == str(end_junction_id))
    session["operations"].append({
        "type": "update_route_direction",
        "routeDirectionId": direction["id"],
        "changes": {
            "startJunctionId": start_node["id"] if start_node else None,
            "endJunctionId": end_node["id"] if end_node else None,
            "customDirectionName": str(custom_direction_name).strip() if custom_direction_name else None,
        },
    })
    return _derive(session)


def stage_route_membership(
    app: Flask,
    token: str,
    route_direction_id: Any,
    path_segment_id: Any,
    traversal: str = "both",
) -> dict[str, Any]:
    if traversal not in TRAVERSALS:
        raise EditSessionValidation("Invalid route traversal.")
    session = _session(app, token)
    derived = _derive(session)
    direction = next((item for item in derived["routeDirections"] if str(item["id"]) == str(route_direction_id)), None)
    if direction is None:
        raise EditSessionValidation("The selected route direction does not exist.")
    route_direction_id = direction["id"]
    segment = next((item for item in derived["network"]["pathSegments"] if str(item["id"]) == str(path_segment_id) and item["state"] in {"saved", "added"}), None)
    if segment is None:
        raise EditSessionValidation("The selected segment does not exist.")
    path_segment_id = segment["id"]
    direction_mode = segment.get("directionMode", segment.get("direction_mode", "bidirectional"))
    if direction_mode != "bidirectional" and traversal not in {"both", direction_mode}:
        raise EditSessionValidation("Route traversal contradicts the segment direction restriction.")
    operation_id = uuid4().hex
    session["operations"].append({
        "type": "assign_route_segment",
        "membershipId": f"route-membership-{operation_id}",
        "routeDirectionId": route_direction_id,
        "pathSegmentId": path_segment_id,
        "traversal": traversal,
    })
    return _derive(session)


def stage_segment_direction(
    app: Flask,
    token: str,
    path_segment_id: Any,
    direction_mode: str,
) -> dict[str, Any]:
    if direction_mode not in SEGMENT_DIRECTION_MODES:
        raise EditSessionValidation("Invalid segment direction mode.")
    session = _session(app, token)
    derived = _derive(session)
    segment = next((item for item in derived["network"]["pathSegments"] if str(item["id"]) == str(path_segment_id) and item["state"] in {"saved", "added"}), None)
    if segment is None:
        raise EditSessionValidation("The selected segment does not exist.")
    for membership in derived["routeMemberships"]:
        if str(membership["pathSegmentId"]) != str(segment["id"]):
            continue
        if direction_mode != "bidirectional" and membership["traversal"] not in {"both", direction_mode}:
            raise EditSessionValidation("An existing route traversal contradicts that direction restriction.")
    operation_id = uuid4().hex
    session["operations"].append({
        "type": "set_segment_direction",
        "pathSegmentId": segment["id"],
        "replacementPathSegmentId": f"direction-path-segment-{operation_id}",
        "directionMode": direction_mode,
    })
    return _derive(session)


def stage_remove_route_membership(
    app: Flask,
    token: str,
    route_direction_id: Any,
    path_segment_id: Any,
) -> dict[str, Any]:
    session = _session(app, token)
    derived = _derive(session)
    if not any(
        str(item["routeDirectionId"]) == str(route_direction_id)
        and str(item["pathSegmentId"]) == str(path_segment_id)
        for item in derived["routeMemberships"]
    ):
        raise EditSessionValidation("That route membership does not exist.")
    session["operations"].append({
        "type": "remove_route_segment",
        "routeDirectionId": route_direction_id,
        "pathSegmentId": path_segment_id,
    })
    return _derive(session)


def cancel_edit_session(app: Flask, token: str) -> bool:
    return _session_store(app).pop(token, None) is not None


def commit_edit_session(app: Flask, token: str) -> dict[str, Any]:
    session = _session(app, token)
    if not session["operations"]:
        raise EditSessionConflict("There are no staged changes to save.")
    derived = _derive(session)
    if derived["isStale"]:
        raise EditSessionConflict(
            "The saved path network changed. Cancel and restart this edit session."
        )
    if (
        derived["import"]
        and derived["import"]["overlapAnalysis"]["hasUnresolvedOverlaps"]
    ):
        raise EditSessionConflict(
            "Review every proposed path overlap before saving."
        )
    try:
        committed = commit_staged_network(
            session["baseRevision"],
            derived["network"],
            {
                "routes": derived["routes"],
                "routeDirections": derived["routeDirections"],
                "routeMemberships": derived["routeMemberships"],
            },
        )
    except RepositoryConflict as error:
        raise EditSessionConflict(str(error)) from error
    except RepositoryError as error:
        raise EditSessionValidation(str(error)) from error

    committed_summary = deepcopy(derived["changeSummary"])
    session["savedNetwork"] = deepcopy(committed["network"])
    session["savedBus"] = deepcopy(get_bus_snapshot())
    session["baseRevision"] = committed["revision"]
    session["operations"] = []
    result = _derive(session)
    result["committed"] = True
    result["committedChangeSummary"] = committed_summary
    return result
