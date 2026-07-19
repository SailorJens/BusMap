from __future__ import annotations

import math
import re
from typing import Any

from path_network.db import get_db
from path_network.repository import RepositoryError


TRAVERSALS = {"both", "start_to_end", "end_to_start"}
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def direction_display_name(direction: dict[str, Any]) -> str | None:
    if direction.get("customDirectionName"):
        return direction["customDirectionName"]
    start_lon = direction.get("startLongitude")
    start_lat = direction.get("startLatitude")
    end_lon = direction.get("endLongitude")
    end_lat = direction.get("endLatitude")
    if None in (start_lon, start_lat, end_lon, end_lat):
        return None
    mean_latitude = math.radians((float(start_lat) + float(end_lat)) / 2)
    east = (float(end_lon) - float(start_lon)) * math.cos(mean_latitude)
    north = float(end_lat) - float(start_lat)
    bearing = (math.degrees(math.atan2(east, north)) + 360) % 360
    if bearing < 45 or bearing >= 315:
        return "Northbound"
    if bearing < 135:
        return "Eastbound"
    if bearing < 225:
        return "Southbound"
    return "Westbound"


def _direction_from_row(row) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "busRouteId": row["bus_route_id"],
        "startJunctionId": row["start_junction_id"],
        "endJunctionId": row["end_junction_id"],
        "customDirectionName": row["custom_direction_name"],
        "startLongitude": row["start_longitude"],
        "startLatitude": row["start_latitude"],
        "endLongitude": row["end_longitude"],
        "endLatitude": row["end_latitude"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    result["displayName"] = direction_display_name(result)
    return result


def list_route_directions(bus_route_id: int | None = None) -> list[dict[str, Any]]:
    parameters: tuple[Any, ...] = ()
    condition = ""
    if bus_route_id is not None:
        condition = "WHERE d.bus_route_id = ?"
        parameters = (bus_route_id,)
    rows = get_db().execute(
        f"""
        SELECT d.*, sj.longitude AS start_longitude, sj.latitude AS start_latitude,
               ej.longitude AS end_longitude, ej.latitude AS end_latitude
        FROM route_directions d
        LEFT JOIN junctions sj ON sj.id = d.start_junction_id
        LEFT JOIN junctions ej ON ej.id = d.end_junction_id
        {condition}
        ORDER BY d.id
        """,
        parameters,
    ).fetchall()
    return [_direction_from_row(row) for row in rows]


def list_routes() -> list[dict[str, Any]]:
    directions = list_route_directions()
    by_route: dict[int, list[dict[str, Any]]] = {}
    for direction in directions:
        by_route.setdefault(direction["busRouteId"], []).append(direction)
    rows = get_db().execute("SELECT * FROM bus_routes ORDER BY route_code COLLATE NOCASE").fetchall()
    return [
        {
            "id": row["id"],
            "routeCode": row["route_code"],
            "displayName": row["display_name"],
            "colour": row["colour"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "directions": by_route.get(row["id"], []),
        }
        for row in rows
    ]


def list_route_memberships() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, route_direction_id, path_segment_id, traversal, created_at
        FROM route_direction_segments ORDER BY id
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "routeDirectionId": row["route_direction_id"],
            "pathSegmentId": row["path_segment_id"],
            "traversal": row["traversal"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def get_bus_snapshot() -> dict[str, Any]:
    return {"routes": list_routes(), "routeMemberships": list_route_memberships()}


def create_route(route_code: str, display_name: str | None = None, colour: str | None = None) -> dict[str, Any]:
    route_code = str(route_code or "").strip()
    if not route_code or len(route_code) > 20:
        raise RepositoryError("Route code is required and must be at most 20 characters.")
    if colour and not COLOUR_RE.fullmatch(colour):
        raise RepositoryError("Route colour must use #RRGGBB format.")
    connection = get_db()
    try:
        cursor = connection.execute(
            "INSERT INTO bus_routes (route_code, display_name, colour) VALUES (?, ?, ?)",
            (route_code, (display_name or "").strip() or None, colour),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        if "UNIQUE" in str(error):
            raise RepositoryError("That route code already exists.") from error
        raise
    return next(route for route in list_routes() if route["id"] == cursor.lastrowid)


def create_direction(
    bus_route_id: int,
    start_junction_id: int | None = None,
    end_junction_id: int | None = None,
    custom_direction_name: str | None = None,
) -> dict[str, Any]:
    if (start_junction_id is None) != (end_junction_id is None):
        raise RepositoryError("Set both direction endpoints or leave both undefined.")
    connection = get_db()
    try:
        cursor = connection.execute(
            """
            INSERT INTO route_directions
                (bus_route_id, start_junction_id, end_junction_id, custom_direction_name)
            VALUES (?, ?, ?, ?)
            """,
            (bus_route_id, start_junction_id, end_junction_id, (custom_direction_name or "").strip() or None),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        raise RepositoryError("The route or one of its endpoint nodes does not exist.") from error
    return next(direction for direction in list_route_directions() if direction["id"] == cursor.lastrowid)


def set_membership(route_direction_id: int, path_segment_id: int, traversal: str) -> dict[str, Any]:
    if traversal not in TRAVERSALS:
        raise RepositoryError("Invalid route traversal.")
    segment = get_db().execute(
        "SELECT direction_mode FROM path_segments WHERE id = ?", (path_segment_id,)
    ).fetchone()
    if segment is None:
        raise RepositoryError("The selected segment does not exist.")
    if segment["direction_mode"] != "bidirectional" and traversal not in {
        "both", segment["direction_mode"]
    }:
        raise RepositoryError("Route traversal contradicts the segment direction restriction.")
    connection = get_db()
    try:
        connection.execute(
            """
            INSERT INTO route_direction_segments
                (route_direction_id, path_segment_id, traversal)
            VALUES (?, ?, ?)
            ON CONFLICT(route_direction_id, path_segment_id)
            DO UPDATE SET traversal = excluded.traversal
            """,
            (route_direction_id, path_segment_id, traversal),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        raise RepositoryError("The selected route direction does not exist.") from error
    return next(
        membership for membership in list_route_memberships()
        if membership["routeDirectionId"] == route_direction_id
        and membership["pathSegmentId"] == path_segment_id
    )


def remove_membership(route_direction_id: int, path_segment_id: int) -> bool:
    connection = get_db()
    cursor = connection.execute(
        "DELETE FROM route_direction_segments WHERE route_direction_id = ? AND path_segment_id = ?",
        (route_direction_id, path_segment_id),
    )
    connection.commit()
    return cursor.rowcount > 0
