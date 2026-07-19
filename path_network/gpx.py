from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import BinaryIO


DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024


class GpxError(ValueError):
    pass


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def haversine_distance(first: list[float], second: list[float]) -> float:
    lon1, lat1 = map(math.radians, first[:2])
    lon2, lat2 = map(math.radians, second[:2])
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def route_stats(segments: list[list[list[float]]]) -> dict[str, float | int]:
    distance = 0.0
    elevation_gain = 0.0
    point_count = 0

    for segment in segments:
        point_count += len(segment)
        for previous, current in zip(segment, segment[1:]):
            distance += haversine_distance(previous, current)
            if len(previous) == 3 and len(current) == 3:
                elevation_gain += max(0.0, current[2] - previous[2])

    return {
        "distanceMeters": round(distance),
        "elevationGainMeters": round(elevation_gain),
        "pointCount": point_count,
    }


def point_coordinates(point: ET.Element) -> list[float] | None:
    try:
        latitude = float(point.attrib["lat"])
        longitude = float(point.attrib["lon"])
    except (KeyError, ValueError):
        return None

    coordinates = [longitude, latitude]
    elevation = child_text(point, "ele")
    if elevation is not None:
        try:
            coordinates.append(float(elevation))
        except ValueError:
            pass
    return coordinates


def parse_gpx(
    stream: BinaryIO,
    filename: str,
    *,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
) -> dict:
    data = stream.read(max_file_size + 1)
    if len(data) > max_file_size:
        raise GpxError(f"File is larger than {max_file_size // (1024 * 1024)} MB.")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise GpxError("DTD and entity declarations are not supported.")

    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise GpxError("The file is not valid XML.") from error

    if local_name(root.tag) != "gpx":
        raise GpxError("The XML document is not a GPX file.")

    segments: list[list[list[float]]] = []
    document_name = None

    for element in root.iter():
        element_name = local_name(element.tag)
        if element_name == "metadata" and document_name is None:
            document_name = child_text(element, "name")
        elif element_name == "trk":
            if document_name is None:
                document_name = child_text(element, "name")
            for track_segment in element:
                if local_name(track_segment.tag) != "trkseg":
                    continue
                coordinates = [
                    coordinate
                    for point in track_segment
                    if local_name(point.tag) == "trkpt"
                    and (coordinate := point_coordinates(point)) is not None
                ]
                if coordinates:
                    segments.append(coordinates)
        elif element_name == "rte":
            if document_name is None:
                document_name = child_text(element, "name")
            coordinates = [
                coordinate
                for point in element
                if local_name(point.tag) == "rtept"
                and (coordinate := point_coordinates(point)) is not None
            ]
            if coordinates:
                segments.append(coordinates)

    if not segments:
        raise GpxError("No track or route points were found.")

    return {
        "name": document_name or Path(filename).stem,
        "filename": filename,
        "segments": segments,
        "stats": route_stats(segments),
    }
