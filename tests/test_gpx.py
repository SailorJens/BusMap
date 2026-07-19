import io

import pytest

from path_network.gpx import GpxError, parse_gpx


def test_parse_gpx_track(sample_gpx_bytes):
    route = parse_gpx(io.BytesIO(sample_gpx_bytes), "berlin.gpx")

    assert route["name"] == "Morning Ride"
    assert route["filename"] == "berlin.gpx"
    assert route["stats"]["pointCount"] == 2
    assert route["stats"]["distanceMeters"] > 0
    assert route["stats"]["elevationGainMeters"] == 12
    assert route["segments"][0][0] == [13.405, 52.52, 30.0]


def test_parse_gpx_route(fixture_path):
    with fixture_path("riverside-route.gpx").open("rb") as stream:
        route = parse_gpx(stream, "riverside-route.gpx")

    assert route["name"] == "Riverside Walk"
    assert route["stats"]["pointCount"] == 3
    assert len(route["segments"]) == 1


def test_rejects_gpx_without_track_or_route_points(fixture_path):
    with fixture_path("empty.gpx").open("rb") as stream:
        with pytest.raises(GpxError, match="No track or route points"):
            parse_gpx(stream, "empty.gpx")


def test_file_size_limit_is_configurable(sample_gpx_bytes):
    with pytest.raises(GpxError, match="larger than 0 MB"):
        parse_gpx(io.BytesIO(sample_gpx_bytes), "track.gpx", max_file_size=10)
