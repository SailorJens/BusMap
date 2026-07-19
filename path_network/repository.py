from __future__ import annotations

import json
import hashlib
from collections.abc import Sequence
from typing import Any

from path_network.db import get_db
from path_network.geometry import geometry_key
from path_network.gpx import haversine_distance


Coordinate = list[float]
SEGMENT_DIRECTION_MODES = {"bidirectional", "start_to_end", "end_to_start"}


class RepositoryError(ValueError):
    pass


class RepositoryConflict(RepositoryError):
    pass


def create_junction(
    longitude: float,
    latitude: float,
    elevation: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    connection = get_db()
    cursor = connection.execute(
        """
        INSERT INTO junctions (longitude, latitude, elevation, metadata_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            longitude,
            latitude,
            elevation,
            json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
        ),
    )
    connection.commit()
    return get_junction(cursor.lastrowid)


def _junction_from_row(row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json"))
    return result


def get_junction(junction_id: int) -> dict[str, Any]:
    row = get_db().execute(
        """
        SELECT id, longitude, latitude, elevation, metadata_json, created_at
        FROM junctions
        WHERE id = ?
        """,
        (junction_id,),
    ).fetchone()
    if row is None:
        raise RepositoryError(f"Junction {junction_id} does not exist.")
    return _junction_from_row(row)


def list_junctions() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, longitude, latitude, elevation, metadata_json, created_at
        FROM junctions
        ORDER BY id
        """
    ).fetchall()
    return [_junction_from_row(row) for row in rows]


def _validate_geometry(
    start_junction: dict[str, Any],
    end_junction: dict[str, Any],
    geometry: Sequence[Sequence[float]],
) -> list[Coordinate]:
    coordinates = [list(map(float, coordinate)) for coordinate in geometry]
    if len(coordinates) < 2 or any(len(coordinate) not in (2, 3) for coordinate in coordinates):
        raise RepositoryError("Path-segment geometry requires at least two valid coordinates.")

    start = coordinates[0]
    end = coordinates[-1]
    if start[:2] != [start_junction["longitude"], start_junction["latitude"]]:
        raise RepositoryError("Geometry must begin at the start junction.")
    if end[:2] != [end_junction["longitude"], end_junction["latitude"]]:
        raise RepositoryError("Geometry must end at the end junction.")
    return coordinates


def create_path_segment(
    start_junction_id: int,
    end_junction_id: int,
    geometry: Sequence[Sequence[float]],
    *,
    source_filename: str | None = None,
    metadata: dict[str, Any] | None = None,
    direction_mode: str = "bidirectional",
) -> dict[str, Any]:
    if direction_mode not in SEGMENT_DIRECTION_MODES:
        raise RepositoryError("Invalid segment direction mode.")
    start_junction = get_junction(start_junction_id)
    end_junction = get_junction(end_junction_id)
    coordinates = _validate_geometry(start_junction, end_junction, geometry)

    longitudes = [coordinate[0] for coordinate in coordinates]
    latitudes = [coordinate[1] for coordinate in coordinates]
    distance = round(
        sum(
            haversine_distance(previous, current)
            for previous, current in zip(coordinates, coordinates[1:])
        )
    )
    bounds = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))

    connection = get_db()
    try:
        cursor = connection.execute(
            """
            INSERT INTO path_segments (
                start_junction_id,
                end_junction_id,
                geometry_json,
                bounds_min_lon,
                bounds_min_lat,
                bounds_max_lon,
                bounds_max_lat,
                distance_m,
                source_filename,
                metadata_json,
                direction_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                start_junction_id,
                end_junction_id,
                json.dumps(coordinates, separators=(",", ":")),
                *bounds,
                distance,
                source_filename,
                json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
                direction_mode,
            ),
        )
        path_segment_id = cursor.lastrowid
        connection.execute(
            """
            INSERT INTO path_segment_bounds (
                path_segment_id,
                min_lon,
                max_lon,
                min_lat,
                max_lat
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (path_segment_id, bounds[0], bounds[2], bounds[1], bounds[3]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return get_path_segment(path_segment_id)


def _path_segment_from_row(row) -> dict[str, Any]:
    result = dict(row)
    result["geometry"] = json.loads(result.pop("geometry_json"))
    result["metadata"] = json.loads(result.pop("metadata_json"))
    return result


def get_path_segment(path_segment_id: int) -> dict[str, Any]:
    row = get_db().execute(
        """
        SELECT *
        FROM path_segments
        WHERE id = ?
        """,
        (path_segment_id,),
    ).fetchone()
    if row is None:
        raise RepositoryError(f"Path segment {path_segment_id} does not exist.")
    return _path_segment_from_row(row)


def list_path_segments() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT *
        FROM path_segments
        ORDER BY id
        """
    ).fetchall()
    return [_path_segment_from_row(row) for row in rows]


def list_path_segments_in_bounds(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT path_segments.*
        FROM path_segment_bounds
        JOIN path_segments
          ON path_segments.id = path_segment_bounds.path_segment_id
        WHERE path_segment_bounds.max_lon >= ?
          AND path_segment_bounds.min_lon <= ?
          AND path_segment_bounds.max_lat >= ?
          AND path_segment_bounds.min_lat <= ?
        ORDER BY path_segments.id
        """,
        (min_longitude, max_longitude, min_latitude, max_latitude),
    ).fetchall()
    return [_path_segment_from_row(row) for row in rows]


def list_junctions_in_bounds(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, longitude, latitude, elevation, metadata_json, created_at
        FROM junctions
        WHERE longitude BETWEEN ? AND ?
          AND latitude BETWEEN ? AND ?
        ORDER BY id
        """,
        (min_longitude, max_longitude, min_latitude, max_latitude),
    ).fetchall()
    return [_junction_from_row(row) for row in rows]


def get_network_bounds(path_segments: Sequence[dict[str, Any]]) -> list[float] | None:
    if not path_segments:
        return None
    return [
        min(segment["bounds_min_lon"] for segment in path_segments),
        min(segment["bounds_min_lat"] for segment in path_segments),
        max(segment["bounds_max_lon"] for segment in path_segments),
        max(segment["bounds_max_lat"] for segment in path_segments),
    ]


def get_path_network() -> dict[str, Any]:
    path_segments = list_path_segments()
    return {
        "junctions": list_junctions(),
        "pathSegments": path_segments,
        "bounds": get_network_bounds(path_segments),
    }


def get_path_network_revision(network: dict[str, Any] | None = None) -> str:
    snapshot = network or get_path_network()
    revision_payload = {
        "junctions": [
            {
                "id": junction["id"],
                "longitude": junction["longitude"],
                "latitude": junction["latitude"],
                "elevation": junction["elevation"],
                "metadata": junction.get("metadata") or {},
            }
            for junction in snapshot["junctions"]
        ],
        "pathSegments": [
            {
                "id": segment["id"],
                "start_junction_id": segment["start_junction_id"],
                "end_junction_id": segment["end_junction_id"],
                "geometry": segment["geometry"],
                "distance_m": segment["distance_m"],
                "source_filename": segment["source_filename"],
                "metadata": segment["metadata"],
                "direction_mode": segment.get("direction_mode", "bidirectional"),
            }
            for segment in snapshot["pathSegments"]
        ],
        "busRoutes": [
            dict(row)
            for row in get_db().execute(
                "SELECT id, route_code, display_name, colour, updated_at FROM bus_routes ORDER BY id"
            ).fetchall()
        ],
        "routeDirections": [
            dict(row)
            for row in get_db().execute(
                """
                SELECT id, bus_route_id, start_junction_id, end_junction_id,
                       custom_direction_name, updated_at
                FROM route_directions ORDER BY id
                """
            ).fetchall()
        ],
        "routeMemberships": [
            dict(row)
            for row in get_db().execute(
                """
                SELECT id, route_direction_id, path_segment_id, traversal
                FROM route_direction_segments ORDER BY id
                """
            ).fetchall()
        ],
    }
    encoded = json.dumps(
        revision_payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _junction_coordinates(junction: dict[str, Any]) -> list[float]:
    coordinate = [float(junction["longitude"]), float(junction["latitude"])]
    if junction.get("elevation") is not None:
        coordinate.append(float(junction["elevation"]))
    return coordinate


def _insert_staged_junction(
    connection,
    junction: dict[str, Any],
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO junctions (longitude, latitude, elevation, metadata_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            junction["longitude"],
            junction["latitude"],
            junction.get("elevation"),
            json.dumps(
                junction.get("metadata") or {},
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )
    return cursor.lastrowid


def _insert_staged_path_segment(
    connection,
    segment: dict[str, Any],
    start_junction_id: int,
    end_junction_id: int,
) -> int:
    coordinates = [list(map(float, coordinate)) for coordinate in segment["geometry"]]
    longitudes = [coordinate[0] for coordinate in coordinates]
    latitudes = [coordinate[1] for coordinate in coordinates]
    bounds = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    distance_metres = round(
        sum(
            haversine_distance(previous, current)
            for previous, current in zip(coordinates, coordinates[1:])
        )
    )
    cursor = connection.execute(
        """
        INSERT INTO path_segments (
            start_junction_id,
            end_junction_id,
            geometry_json,
            bounds_min_lon,
            bounds_min_lat,
            bounds_max_lon,
            bounds_max_lat,
            distance_m,
            source_filename,
            metadata_json,
            direction_mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            start_junction_id,
            end_junction_id,
            json.dumps(coordinates, separators=(",", ":")),
            *bounds,
            distance_metres,
            segment.get("sourceFilename") or segment.get("source_filename"),
            json.dumps(
                segment.get("metadata") or {},
                separators=(",", ":"),
                sort_keys=True,
            ),
            segment.get("directionMode", segment.get("direction_mode", "bidirectional")),
        ),
    )
    path_segment_id = cursor.lastrowid
    connection.execute(
        """
        INSERT INTO path_segment_bounds (
            path_segment_id,
            min_lon,
            max_lon,
            min_lat,
            max_lat
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (path_segment_id, bounds[0], bounds[2], bounds[1], bounds[3]),
    )
    return path_segment_id


def _saved_geometry_key_counts(connection) -> dict[tuple[tuple[float, ...], ...], int]:
    counts: dict[tuple[tuple[float, ...], ...], int] = {}
    rows = connection.execute("SELECT geometry_json FROM path_segments").fetchall()
    for row in rows:
        key = geometry_key(json.loads(row["geometry_json"]))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _validate_staged_commit(
    staged_network: dict[str, Any],
    existing_geometry_counts: dict[tuple[tuple[float, ...], ...], int],
) -> None:
    junctions_by_id = {
        junction["id"]: junction
        for junction in staged_network["junctions"]
        if junction["state"] != "deleted"
    }
    final_segments = [
        segment
        for segment in staged_network["pathSegments"]
        if segment["state"] in {"saved", "added"}
    ]
    final_geometry_counts: dict[tuple[tuple[float, ...], ...], int] = {}

    for segment in final_segments:
        geometry = [list(map(float, coordinate)) for coordinate in segment["geometry"]]
        if len(geometry) < 2 or len({tuple(point[:2]) for point in geometry}) < 2:
            raise RepositoryError("Every saved path segment needs two distinct coordinates.")
        try:
            start_junction_id = segment.get(
                "startJunctionId",
                segment.get("start_junction_id"),
            )
            end_junction_id = segment.get(
                "endJunctionId",
                segment.get("end_junction_id"),
            )
            start_junction = junctions_by_id[start_junction_id]
            end_junction = junctions_by_id[end_junction_id]
        except KeyError as error:
            raise RepositoryError("A staged path segment references a missing junction.") from error
        if geometry[0][:2] != _junction_coordinates(start_junction)[:2]:
            raise RepositoryError("Staged geometry does not begin at its start junction.")
        if geometry[-1][:2] != _junction_coordinates(end_junction)[:2]:
            raise RepositoryError("Staged geometry does not end at its end junction.")
        key = geometry_key(geometry)
        final_geometry_counts[key] = final_geometry_counts.get(key, 0) + 1

    for key, final_count in final_geometry_counts.items():
        if final_count > 1 and final_count > existing_geometry_counts.get(key, 0):
            raise RepositoryError("The staged network contains duplicate path geometry.")


def commit_staged_network(
    base_revision: str,
    staged_network: dict[str, Any],
    staged_bus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if get_path_network_revision() != base_revision:
            raise RepositoryConflict(
                "The saved path network changed after this edit session began."
            )
        _validate_staged_commit(
            staged_network,
            _saved_geometry_key_counts(connection),
        )

        replaced_segment_ids = [
            int(segment["id"])
            for segment in staged_network["pathSegments"]
            if segment["state"] in {"deleted", "replaced"}
            and isinstance(segment["id"], int)
        ]
        inherited_memberships: dict[int, list[tuple[int, str]]] = {}
        for path_segment_id in replaced_segment_ids:
            inherited_memberships[path_segment_id] = [
                (row["route_direction_id"], row["traversal"])
                for row in connection.execute(
                    """
                    SELECT route_direction_id, traversal
                    FROM route_direction_segments
                    WHERE path_segment_id = ?
                    """,
                    (path_segment_id,),
                ).fetchall()
            ]
        orphan_candidate_ids: set[int] = {
            int(junction["id"])
            for junction in staged_network["junctions"]
            if junction["state"] == "deleted"
            and isinstance(junction["id"], int)
        }
        for path_segment_id in replaced_segment_ids:
            endpoint_row = connection.execute(
                """
                SELECT start_junction_id, end_junction_id
                FROM path_segments
                WHERE id = ?
                """,
                (path_segment_id,),
            ).fetchone()
            if endpoint_row is None:
                raise RepositoryConflict(
                    f"Path segment {path_segment_id} is no longer available."
                )
            orphan_candidate_ids.update(endpoint_row)
            connection.execute(
                "DELETE FROM path_segment_bounds WHERE path_segment_id = ?",
                (path_segment_id,),
            )
            cursor = connection.execute(
                "DELETE FROM path_segments WHERE id = ?",
                (path_segment_id,),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(
                    f"Path segment {path_segment_id} is no longer available."
                )

        junction_id_map: dict[str, int] = {}
        for junction in staged_network["junctions"]:
            if junction["state"] == "added":
                permanent_id = _insert_staged_junction(
                    connection,
                    junction,
                )
                junction_id_map[str(junction["id"])] = permanent_id
                if not junction.get("preserveOrphan"):
                    orphan_candidate_ids.add(permanent_id)

        existing_junction_ids = {
            row["id"]
            for row in connection.execute("SELECT id FROM junctions").fetchall()
        }

        for junction in staged_network["junctions"]:
            if (
                isinstance(junction["id"], int)
                and junction["id"] in existing_junction_ids
                and junction["state"] not in {"deleted"}
            ):
                connection.execute(
                    """
                    UPDATE junctions
                    SET metadata_json = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(
                            junction.get("metadata") or {},
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        junction["id"],
                    ),
                )

        def resolve_junction_id(identifier: Any) -> int:
            if isinstance(identifier, int):
                if identifier not in existing_junction_ids:
                    raise RepositoryConflict(
                        f"Junction {identifier} is no longer available."
                    )
                return identifier
            try:
                return junction_id_map[str(identifier)]
            except KeyError as error:
                raise RepositoryError(
                    f"Temporary junction {identifier} was not staged for insertion."
                ) from error

        segment_id_map: dict[str, int] = {}
        for segment in staged_network["pathSegments"]:
            if segment["state"] != "added":
                continue
            permanent_segment_id = _insert_staged_path_segment(
                connection,
                segment,
                resolve_junction_id(segment["startJunctionId"]),
                resolve_junction_id(segment["endJunctionId"]),
            )
            segment_id_map[str(segment["id"])] = permanent_segment_id
            source_id = segment.get("sourcePathSegmentId")
            if isinstance(source_id, int):
                for route_direction_id, traversal in inherited_memberships.get(source_id, []):
                    connection.execute(
                        """
                        INSERT INTO route_direction_segments
                            (route_direction_id, path_segment_id, traversal)
                        VALUES (?, ?, ?)
                        """,
                        (route_direction_id, permanent_segment_id, traversal),
                    )

        for junction_id in orphan_candidate_ids:
            row = connection.execute(
                "SELECT metadata_json FROM junctions WHERE id = ?",
                (junction_id,),
            ).fetchone()
            if row is not None and json.loads(row["metadata_json"]).get("protected"):
                continue
            connection.execute(
                """
                DELETE FROM junctions
                WHERE id = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM path_segments
                    WHERE path_segments.start_junction_id = junctions.id
                       OR path_segments.end_junction_id = junctions.id
                  )
                """,
                (junction_id,),
            )

        if staged_bus is not None:
            route_id_map: dict[str, int] = {}
            for route in staged_bus["routes"]:
                if route.get("state") == "deleted":
                    if isinstance(route["id"], int):
                        connection.execute("DELETE FROM bus_routes WHERE id = ?", (route["id"],))
                    continue
                if route.get("state") == "added":
                    cursor = connection.execute(
                        "INSERT INTO bus_routes (route_code, display_name, colour) VALUES (?, ?, ?)",
                        (route["routeCode"], route.get("displayName"), route.get("colour")),
                    )
                    route_id_map[str(route["id"])] = cursor.lastrowid

            def resolve_route_id(identifier: Any) -> int:
                return identifier if isinstance(identifier, int) else route_id_map[str(identifier)]

            direction_id_map: dict[str, int] = {}
            for direction in staged_bus["routeDirections"]:
                if direction.get("state") == "deleted":
                    if isinstance(direction["id"], int):
                        connection.execute("DELETE FROM route_directions WHERE id = ?", (direction["id"],))
                    continue
                if direction.get("state") == "added":
                    cursor = connection.execute(
                        """
                        INSERT INTO route_directions (
                            bus_route_id, start_junction_id, end_junction_id, custom_direction_name
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            resolve_route_id(direction["busRouteId"]),
                            resolve_junction_id(direction["startJunctionId"])
                            if direction.get("startJunctionId") is not None else None,
                            resolve_junction_id(direction["endJunctionId"])
                            if direction.get("endJunctionId") is not None else None,
                            direction.get("customDirectionName"),
                        ),
                    )
                    direction_id_map[str(direction["id"])] = cursor.lastrowid
                elif direction.get("state") == "updated" and isinstance(direction["id"], int):
                    connection.execute(
                        """
                        UPDATE route_directions
                        SET start_junction_id = ?, end_junction_id = ?,
                            custom_direction_name = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            resolve_junction_id(direction["startJunctionId"]),
                            resolve_junction_id(direction["endJunctionId"]),
                            direction.get("customDirectionName"), direction["id"],
                        ),
                    )

            def resolve_direction_id(identifier: Any) -> int:
                return identifier if isinstance(identifier, int) else direction_id_map[str(identifier)]

            connection.execute("DELETE FROM route_direction_segments")
            for membership in staged_bus["routeMemberships"]:
                if membership.get("state") == "deleted":
                    continue
                segment_identifier = membership["pathSegmentId"]
                permanent_segment_id = (
                    segment_identifier
                    if isinstance(segment_identifier, int)
                    else segment_id_map[str(segment_identifier)]
                )
                connection.execute(
                    """
                    INSERT INTO route_direction_segments
                        (route_direction_id, path_segment_id, traversal)
                    VALUES (?, ?, ?)
                    """,
                    (
                        resolve_direction_id(membership["routeDirectionId"]),
                        permanent_segment_id,
                        membership["traversal"],
                    ),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    network = get_path_network()
    return {
        "network": network,
        "revision": get_path_network_revision(network),
    }
