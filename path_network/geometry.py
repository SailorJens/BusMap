from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


EARTH_RADIUS_METRES = 6_371_000.0
INTERSECTION_EPSILON = 1e-8


@dataclass(frozen=True)
class Projector:
    longitude_origin: float
    latitude_origin: float

    @property
    def longitude_scale(self) -> float:
        return EARTH_RADIUS_METRES * math.cos(math.radians(self.latitude_origin))

    def project(self, coordinate: Sequence[float]) -> tuple[float, float]:
        return (
            math.radians(float(coordinate[0]) - self.longitude_origin)
            * self.longitude_scale,
            math.radians(float(coordinate[1]) - self.latitude_origin)
            * EARTH_RADIUS_METRES,
        )

    def unproject(self, point: Sequence[float]) -> list[float]:
        return [
            self.longitude_origin
            + math.degrees(float(point[0]) / self.longitude_scale),
            self.latitude_origin
            + math.degrees(float(point[1]) / EARTH_RADIUS_METRES),
        ]


def projector_for(geometries: Iterable[Sequence[Sequence[float]]]) -> Projector:
    coordinates = [coordinate for geometry in geometries for coordinate in geometry]
    if not coordinates:
        raise ValueError("At least one coordinate is required.")
    return Projector(
        sum(float(coordinate[0]) for coordinate in coordinates) / len(coordinates),
        sum(float(coordinate[1]) for coordinate in coordinates) / len(coordinates),
    )


def remove_consecutive_duplicates(
    geometry: Sequence[Sequence[float]],
) -> list[list[float]]:
    result: list[list[float]] = []
    for coordinate in geometry:
        normalized = list(map(float, coordinate))
        if not result or normalized[:2] != result[-1][:2]:
            result.append(normalized)
    return result


def expanded_wgs84_bounds(
    geometry: Sequence[Sequence[float]],
    tolerance_metres: float,
) -> tuple[float, float, float, float]:
    latitudes = [float(coordinate[1]) for coordinate in geometry]
    longitudes = [float(coordinate[0]) for coordinate in geometry]
    latitude = sum(latitudes) / len(latitudes)
    latitude_padding = math.degrees(tolerance_metres / EARTH_RADIUS_METRES)
    longitude_padding = math.degrees(
        tolerance_metres
        / (EARTH_RADIUS_METRES * max(math.cos(math.radians(latitude)), 1e-9))
    )
    return (
        min(longitudes) - longitude_padding,
        min(latitudes) - latitude_padding,
        max(longitudes) + longitude_padding,
        max(latitudes) + latitude_padding,
    )


def distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(second[0]) - float(first[0]), float(second[1]) - float(first[1]))


def interpolate_coordinate(
    first: Sequence[float],
    second: Sequence[float],
    fraction: float,
) -> list[float]:
    coordinate = [
        float(first[0]) + (float(second[0]) - float(first[0])) * fraction,
        float(first[1]) + (float(second[1]) - float(first[1])) * fraction,
    ]
    if len(first) == 3 and len(second) == 3:
        coordinate.append(float(first[2]) + (float(second[2]) - float(first[2])) * fraction)
    return coordinate


def project_point_to_leg(
    point: Sequence[float],
    start: Sequence[float],
    end: Sequence[float],
) -> tuple[float, tuple[float, float], float]:
    delta_x = float(end[0]) - float(start[0])
    delta_y = float(end[1]) - float(start[1])
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0:
        projected = (float(start[0]), float(start[1]))
        return 0.0, projected, distance(point, projected)
    fraction = (
        (float(point[0]) - float(start[0])) * delta_x
        + (float(point[1]) - float(start[1])) * delta_y
    ) / length_squared
    fraction = min(1.0, max(0.0, fraction))
    projected = (
        float(start[0]) + delta_x * fraction,
        float(start[1]) + delta_y * fraction,
    )
    return fraction, projected, distance(point, projected)


def project_point_to_geometry(
    point: Sequence[float],
    geometry: Sequence[Sequence[float]],
) -> tuple[float, list[float], float]:
    if len(geometry) < 2:
        raise ValueError("Geometry requires at least two coordinates.")

    projector = projector_for([geometry, [point]])
    projected_point = projector.project(point)
    projected_geometry = [projector.project(coordinate) for coordinate in geometry]
    candidates = []
    for leg_index, (start, end) in enumerate(
        zip(projected_geometry, projected_geometry[1:])
    ):
        fraction, _projected, separation = project_point_to_leg(
            projected_point,
            start,
            end,
        )
        position = position_on_geometry(leg_index, fraction)
        candidates.append(
            (
                separation,
                position,
                coordinate_at_position(geometry, position),
            )
        )

    separation, position, coordinate = min(candidates, key=lambda item: item[0])
    return position, coordinate, separation


def leg_intersection(
    first_start: Sequence[float],
    first_end: Sequence[float],
    second_start: Sequence[float],
    second_end: Sequence[float],
) -> tuple[float, float, tuple[float, float]] | None:
    first_x = float(first_end[0]) - float(first_start[0])
    first_y = float(first_end[1]) - float(first_start[1])
    second_x = float(second_end[0]) - float(second_start[0])
    second_y = float(second_end[1]) - float(second_start[1])
    denominator = first_x * second_y - first_y * second_x
    if abs(denominator) <= INTERSECTION_EPSILON:
        contacts: list[tuple[float, float, tuple[float, float]]] = []
        for first_fraction, point in (
            (0.0, first_start),
            (1.0, first_end),
        ):
            second_fraction, projected, separation = project_point_to_leg(
                point, second_start, second_end
            )
            if separation <= INTERSECTION_EPSILON:
                contacts.append(
                    (first_fraction, second_fraction, (float(projected[0]), float(projected[1])))
                )
        for second_fraction, point in (
            (0.0, second_start),
            (1.0, second_end),
        ):
            first_fraction, projected, separation = project_point_to_leg(
                point, first_start, first_end
            )
            if separation <= INTERSECTION_EPSILON:
                contacts.append(
                    (first_fraction, second_fraction, (float(projected[0]), float(projected[1])))
                )
        unique_contacts: list[tuple[float, float, tuple[float, float]]] = []
        for contact in contacts:
            if not any(distance(contact[2], existing[2]) <= INTERSECTION_EPSILON for existing in unique_contacts):
                unique_contacts.append(contact)
        return unique_contacts[0] if len(unique_contacts) == 1 else None

    offset_x = float(second_start[0]) - float(first_start[0])
    offset_y = float(second_start[1]) - float(first_start[1])
    first_fraction = (offset_x * second_y - offset_y * second_x) / denominator
    second_fraction = (offset_x * first_y - offset_y * first_x) / denominator
    if not (
        -INTERSECTION_EPSILON <= first_fraction <= 1 + INTERSECTION_EPSILON
        and -INTERSECTION_EPSILON <= second_fraction <= 1 + INTERSECTION_EPSILON
    ):
        return None

    first_fraction = min(1.0, max(0.0, first_fraction))
    second_fraction = min(1.0, max(0.0, second_fraction))
    return (
        first_fraction,
        second_fraction,
        (
            float(first_start[0]) + first_x * first_fraction,
            float(first_start[1]) + first_y * first_fraction,
        ),
    )


def position_on_geometry(leg_index: int, fraction: float) -> float:
    return float(leg_index) + fraction


def coordinate_at_position(
    geometry: Sequence[Sequence[float]],
    position: float,
) -> list[float]:
    if position <= 0:
        return list(map(float, geometry[0]))
    last_position = len(geometry) - 1
    if position >= last_position:
        return list(map(float, geometry[-1]))
    leg_index = int(math.floor(position))
    return interpolate_coordinate(
        geometry[leg_index],
        geometry[leg_index + 1],
        position - leg_index,
    )


def split_geometry(
    geometry: Sequence[Sequence[float]],
    cuts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for cut in sorted(cuts, key=lambda item: float(item["position"])):
        normalized = {
            **cut,
            "position": min(len(geometry) - 1, max(0.0, float(cut["position"]))),
            "coordinate": list(map(float, cut["coordinate"])),
        }
        if ordered and abs(normalized["position"] - ordered[-1]["position"]) < 1e-9:
            ordered[-1] = normalized
        else:
            ordered.append(normalized)

    pieces: list[dict[str, Any]] = []
    for start_cut, end_cut in zip(ordered, ordered[1:]):
        if end_cut["position"] - start_cut["position"] < 1e-9:
            continue
        coordinates = [start_cut["coordinate"]]
        first_vertex = math.floor(start_cut["position"]) + 1
        last_vertex = math.ceil(end_cut["position"])
        for vertex_index in range(first_vertex, last_vertex):
            coordinate = list(map(float, geometry[vertex_index]))
            if coordinate[:2] != coordinates[-1][:2]:
                coordinates.append(coordinate)
        if end_cut["coordinate"][:2] != coordinates[-1][:2]:
            coordinates.append(end_cut["coordinate"])
        if len(coordinates) >= 2 and len({tuple(point[:2]) for point in coordinates}) >= 2:
            pieces.append(
                {
                    "startNode": start_cut["node"],
                    "endNode": end_cut["node"],
                    "geometry": coordinates,
                }
            )
    return pieces


def geometry_key(
    geometry: Sequence[Sequence[float]],
    *,
    precision: int = 8,
) -> tuple[tuple[float, ...], ...]:
    forward = tuple(
        tuple(round(float(value), precision) for value in coordinate[:2])
        for coordinate in geometry
    )
    reverse = tuple(reversed(forward))
    return min(forward, reverse)
