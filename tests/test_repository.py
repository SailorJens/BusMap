import sqlite3

import pytest

from path_network import create_app
from path_network.db import get_db
from path_network.repository import (
    RepositoryError,
    create_junction,
    create_path_segment,
    commit_staged_network,
    get_path_network,
    get_path_network_revision,
    list_junctions,
    list_path_segments,
    list_path_segments_in_bounds,
)


def create_sample_segment():
    start = create_junction(13.405, 52.52, 30)
    end = create_junction(13.41, 52.521, 42)
    segment = create_path_segment(
        start["id"],
        end["id"],
        [
            [13.405, 52.52, 30],
            [13.407, 52.5205, 34],
            [13.41, 52.521, 42],
        ],
        source_filename="morning-ride.gpx",
        metadata={"surface": "unknown"},
    )
    return start, end, segment


def test_schema_is_initialized(app):
    with app.app_context():
        tables = {
            row["name"]
            for row in get_db()
            .execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'virtual table')
                """
            )
            .fetchall()
        }

    assert {"junctions", "path_segments", "path_segment_bounds"} <= tables


def test_create_and_list_path_network(app):
    with app.app_context():
        start, end, segment = create_sample_segment()
        network = get_path_network()

    assert [junction["id"] for junction in network["junctions"]] == [start["id"], end["id"]]
    assert network["pathSegments"][0]["id"] == segment["id"]
    assert network["pathSegments"][0]["geometry"][1] == [13.407, 52.5205, 34.0]
    assert network["pathSegments"][0]["metadata"] == {"surface": "unknown"}
    assert network["bounds"] == [13.405, 52.52, 13.41, 52.521]


def test_rtree_bounds_match_path_segment(app):
    with app.app_context():
        _start, _end, segment = create_sample_segment()
        bounds = get_db().execute(
            """
            SELECT min_lon, max_lon, min_lat, max_lat
            FROM path_segment_bounds
            WHERE path_segment_id = ?
            """,
            (segment["id"],),
        ).fetchone()

    assert bounds["min_lon"] == pytest.approx(segment["bounds_min_lon"], abs=0.00001)
    assert bounds["max_lon"] == pytest.approx(segment["bounds_max_lon"], abs=0.00001)
    assert bounds["min_lat"] == pytest.approx(segment["bounds_min_lat"], abs=0.00001)
    assert bounds["max_lat"] == pytest.approx(segment["bounds_max_lat"], abs=0.00001)


def test_rtree_query_shortlists_overlapping_path_segments(app):
    with app.app_context():
        _start, _end, segment = create_sample_segment()
        far_start = create_junction(10.0, 48.0)
        far_end = create_junction(10.1, 48.1)
        create_path_segment(
            far_start["id"],
            far_end["id"],
            [[10.0, 48.0], [10.1, 48.1]],
        )

        matches = list_path_segments_in_bounds(13.406, 52.519, 13.411, 52.522)

    assert [match["id"] for match in matches] == [segment["id"]]


def test_geometry_must_match_endpoint_junctions(app):
    with app.app_context():
        start = create_junction(13.405, 52.52)
        end = create_junction(13.41, 52.521)

        with pytest.raises(RepositoryError, match="begin at the start junction"):
            create_path_segment(
                start["id"],
                end["id"],
                [[13.406, 52.52], [13.41, 52.521]],
            )

        assert list_path_segments() == []
        assert len(list_junctions()) == 2


def test_saved_network_survives_new_app_instance(tmp_path):
    instance_path = tmp_path / "instance"
    database_path = tmp_path / "network.sqlite"
    config = {"TESTING": True, "DATABASE": str(database_path)}

    first_app = create_app(config, instance_path=instance_path)
    with first_app.app_context():
        _start, _end, segment = create_sample_segment()

    second_app = create_app(config, instance_path=instance_path)
    with second_app.app_context():
        saved_segments = list_path_segments()

    assert saved_segments[0]["id"] == segment["id"]
    assert database_path.is_file()


def test_foreign_keys_are_enabled(app):
    with app.app_context():
        enabled = get_db().execute("PRAGMA foreign_keys").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            get_db().execute(
                """
                INSERT INTO path_segments (
                    start_junction_id, end_junction_id, geometry_json,
                    bounds_min_lon, bounds_min_lat, bounds_max_lon,
                    bounds_max_lat, distance_m
                )
                VALUES (999, 1000, '[[0,0],[1,1]]', 0, 0, 1, 1, 1)
                """
            )

    assert enabled == 1


def test_atomic_commit_removes_only_touched_orphan_junctions(app):
    with app.app_context():
        old_start = create_junction(13.0, 52.0)
        old_end = create_junction(13.001, 52.0)
        untouched_orphan = create_junction(14.0, 53.0)
        old_segment = create_path_segment(
            old_start["id"],
            old_end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        snapshot = get_path_network()
        base_revision = get_path_network_revision(snapshot)
        staged_network = {
            "junctions": [
                *[{**junction, "state": "saved"} for junction in snapshot["junctions"]],
                {
                    "id": "draft-junction-1",
                    "longitude": 13.01,
                    "latitude": 52.01,
                    "elevation": None,
                    "state": "added",
                },
                {
                    "id": "draft-junction-2",
                    "longitude": 13.02,
                    "latitude": 52.02,
                    "elevation": None,
                    "state": "added",
                },
            ],
            "pathSegments": [
                {**old_segment, "state": "replaced"},
                {
                    "id": "draft-path-segment-1",
                    "startJunctionId": "draft-junction-1",
                    "endJunctionId": "draft-junction-2",
                    "geometry": [[13.01, 52.01], [13.02, 52.02]],
                    "sourceFilename": "replacement.gpx",
                    "metadata": {},
                    "state": "added",
                },
            ],
            "bounds": snapshot["bounds"],
        }

        result = commit_staged_network(base_revision, staged_network)

        remaining_ids = {junction["id"] for junction in result["network"]["junctions"]}
    assert old_start["id"] not in remaining_ids
    assert old_end["id"] not in remaining_ids
    assert untouched_orphan["id"] in remaining_ids


def test_commit_allows_preexisting_saved_duplicate_geometry(app):
    with app.app_context():
        first_start = create_junction(13.0, 52.0)
        first_end = create_junction(13.001, 52.0)
        second_start = create_junction(13.0, 52.0)
        second_end = create_junction(13.001, 52.0)
        create_path_segment(
            first_start["id"],
            first_end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        create_path_segment(
            second_start["id"],
            second_end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        snapshot = get_path_network()
        base_revision = get_path_network_revision(snapshot)
        staged_network = {
            "junctions": [
                {**junction, "state": "saved"}
                for junction in snapshot["junctions"]
            ],
            "pathSegments": [
                {**segment, "state": "saved"}
                for segment in snapshot["pathSegments"]
            ],
            "bounds": snapshot["bounds"],
        }

        result = commit_staged_network(base_revision, staged_network)

    assert len(result["network"]["pathSegments"]) == 2


def test_commit_rejects_new_duplicate_of_saved_geometry(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.001, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        snapshot = get_path_network()
        base_revision = get_path_network_revision(snapshot)
        staged_network = {
            "junctions": [
                {**junction, "state": "saved"}
                for junction in snapshot["junctions"]
            ],
            "pathSegments": [
                {**segment, "state": "saved"}
                for segment in snapshot["pathSegments"]
            ]
            + [
                {
                    "id": "draft-path-segment-1",
                    "startJunctionId": start["id"],
                    "endJunctionId": end["id"],
                    "geometry": [[13.0, 52.0], [13.001, 52.0]],
                    "sourceFilename": "duplicate.gpx",
                    "metadata": {},
                    "state": "added",
                }
            ],
            "bounds": snapshot["bounds"],
        }

        with pytest.raises(RepositoryError, match="duplicate path geometry"):
            commit_staged_network(base_revision, staged_network)
