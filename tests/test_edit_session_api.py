import io

import pytest

from path_network import repository
from path_network.db import get_db
from path_network.edit_session import _derive_orphan_junctions
from path_network.repository import create_junction, create_path_segment


def database_counts(app):
    with app.app_context():
        junction_count = get_db().execute("SELECT COUNT(*) FROM junctions").fetchone()[0]
        segment_count = get_db().execute("SELECT COUNT(*) FROM path_segments").fetchone()[0]
    return junction_count, segment_count


def create_session(client):
    response = client.post("/api/edit-sessions")
    assert response.status_code == 201
    return response.get_json()


def test_create_edit_session_returns_saved_snapshot_and_revision(client):
    payload = create_session(client)

    assert payload["token"]
    assert len(payload["baseRevision"]) == 64
    assert payload["operations"] == []
    assert payload["historyPosition"] == 0
    assert payload["canUndo"] is False
    assert payload["canCommit"] is False
    assert payload["isStale"] is False
    assert payload["network"] == {
        "bounds": None,
        "junctions": [],
        "pathSegments": [],
    }


def test_import_is_one_operation_and_undo_restores_saved_view(
    app, client, sample_gpx_bytes
):
    session = create_session(client)
    before = database_counts(app)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert [operation["type"] for operation in staged["operations"]] == ["import_trace"]
    assert staged["canUndo"] is True
    assert staged["historyPosition"] == 1
    assert staged["changeSummary"]["operationCount"] == 1
    assert staged["changeSummary"]["addedJunctions"] == 2
    assert staged["changeSummary"]["addedPathSegments"] == 1
    assert {junction["state"] for junction in staged["network"]["junctions"]} == {"added"}
    assert {segment["state"] for segment in staged["network"]["pathSegments"]} == {"added"}
    assert database_counts(app) == before

    undo_response = client.post(f"/api/edit-sessions/{session['token']}/undo")
    undone = undo_response.get_json()

    assert undo_response.status_code == 200
    assert undone["operations"] == []
    assert undone["historyPosition"] == 0
    assert undone["network"] == session["network"]
    assert undone["changeSummary"]["operationCount"] == 0
    assert database_counts(app) == before


def test_commit_persists_import_and_starts_clean_session(
    app, client, sample_gpx_bytes
):
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    ).get_json()

    assert staged["canCommit"] is True
    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert committed["committed"] is True
    assert committed["operations"] == []
    assert committed["canCommit"] is False
    assert committed["baseRevision"] != session["baseRevision"]
    assert committed["committedChangeSummary"]["addedJunctions"] == 2
    assert committed["committedChangeSummary"]["addedPathSegments"] == 1
    assert database_counts(app) == (2, 1)

    refreshed = client.get("/api/path-network").get_json()
    assert {junction["state"] for junction in committed["network"]["junctions"]} == {"saved"}
    assert {segment["state"] for segment in committed["network"]["pathSegments"]} == {"saved"}
    assert refreshed["bounds"] == committed["network"]["bounds"]
    assert [
        {key: value for key, value in junction.items() if key != "state"}
        for junction in committed["network"]["junctions"]
    ] == refreshed["junctions"]
    assert [
        {key: value for key, value in segment.items() if key != "state"}
        for segment in committed["network"]["pathSegments"]
    ] == refreshed["pathSegments"]
    with app.app_context():
        indexed_count = get_db().execute(
            "SELECT COUNT(*) FROM path_segment_bounds"
        ).fetchone()[0]
    assert indexed_count == 1


def test_second_disjoint_import_keeps_first_saved_network_in_saved_state(client):
    session = create_session(client)

    def gpx(points):
        track_points = "".join(
            f'<trkpt lat="{latitude}" lon="{longitude}"/>'
            for longitude, latitude in points
        )
        return (
            '<?xml version="1.0"?>'
            '<gpx version="1.1"><trk><trkseg>'
            f"{track_points}"
            "</trkseg></trk></gpx>"
        ).encode()

    first = gpx([(13.0, 52.0), (13.001, 52.0)])
    second = gpx([(14.0, 53.0), (14.001, 53.0)])
    client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(first), "first.gpx")},
        content_type="multipart/form-data",
    )
    client.post(f"/api/edit-sessions/{session['token']}/commit")

    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(second), "second.gpx")},
        content_type="multipart/form-data",
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert [junction["state"] for junction in staged["network"]["junctions"]] == [
        "saved",
        "saved",
        "added",
        "added",
    ]
    assert [segment["state"] for segment in staged["network"]["pathSegments"]] == [
        "saved",
        "added",
    ]


def test_crossing_import_marks_saved_path_replaced_and_additions_added(
    app, client
):
    with app.app_context():
        west = create_junction(13.404, 52.5205)
        east = create_junction(13.411, 52.5205)
        saved = create_path_segment(
            west["id"],
            east["id"],
            [[13.404, 52.5205], [13.411, 52.5205]],
            metadata={"surface": "gravel"},
        )
    session = create_session(client)
    before = database_counts(app)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(
            b"""<?xml version="1.0"?>
            <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
              <trk><trkseg>
                <trkpt lat="52.519" lon="13.407"/>
                <trkpt lat="52.522" lon="13.407"/>
              </trkseg></trk>
            </gpx>"""
        ), "crossing.gpx")},
        content_type="multipart/form-data",
    )
    staged = response.get_json()

    saved_state = next(
        segment["state"]
        for segment in staged["network"]["pathSegments"]
        if segment["id"] == saved["id"]
    )
    added_segments = [
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    ]
    assert response.status_code == 201
    assert saved_state == "replaced"
    assert len(added_segments) == 4
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    assert database_counts(app) == before


def test_complete_overlap_automatically_reuses_saved_path_and_allows_override(
    app,
    client,
):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.000018" lon="13.0"/>
                        <trkpt lat="52.000018" lon="13.001"/>
                        <trkpt lat="52.000018" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "noisy-overlap.gpx",
            )
        },
        content_type="multipart/form-data",
    )
    staged = response.get_json()

    assert response.status_code == 201
    analysis = staged["import"]["overlapAnalysis"]
    assert analysis["config"]["version"] == "diagnostics-v1"
    assert analysis["summary"]["candidateCount"] == 1
    candidate = analysis["candidates"][0]
    assert candidate["confidence"] == "high"
    assert candidate["reviewType"] == "complete_section_reuse"
    assert candidate["decision"] == "reuse"
    assert candidate["decisionSource"] == "automatic"
    assert analysis["hasUnresolvedOverlaps"] is False
    assert staged["canCommit"] is True
    assert staged["changeSummary"]["addedPathSegments"] == 0

    reuse_response = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}",
        json={"decision": "reuse"},
    )
    reused = reuse_response.get_json()
    assert reuse_response.status_code == 200
    assert reused["canCommit"] is True
    assert reused["changeSummary"]["addedPathSegments"] == 0
    assert reused["changeSummary"]["addedJunctions"] == 0
    assert reused["import"]["overlapAnalysis"]["summary"]["reusedDistanceMetres"] > 0
    assert reused["import"]["overlapAnalysis"]["summary"]["newDistanceMetres"] == 0

    reset_response = client.delete(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}"
    )
    reset = reset_response.get_json()
    assert reset_response.status_code == 200
    assert reset["canCommit"] is True
    assert reset["changeSummary"]["addedPathSegments"] == 0
    reset_candidate = next(
        item
        for item in reset["import"]["overlapAnalysis"]["candidates"]
        if item["key"] == candidate["key"]
    )
    assert reset_candidate["decision"] == "reuse"
    assert reset_candidate["decisionSource"] == "automatic"

    keep_response = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}",
        json={"decision": "keep"},
    )
    kept = keep_response.get_json()
    assert keep_response.status_code == 200
    assert kept["canCommit"] is True
    assert kept["changeSummary"]["addedPathSegments"] == 1
    assert kept["import"]["overlapAnalysis"]["summary"]["keptCandidateCount"] == 1
    reloaded = client.get(
        f"/api/edit-sessions/{session['token']}"
    ).get_json()
    assert next(
        item
        for item in reloaded["import"]["overlapAnalysis"]["candidates"]
        if item["key"] == candidate["key"]
    )["decision"] == "keep"
    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["operations"] == []
    assert undone["import"] is None


def test_reusing_complete_overlap_commits_no_duplicate_geometry(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        saved = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.000015" lon="13.0"/>
                        <trkpt lat="51.99999" lon="13.001"/>
                        <trkpt lat="52.00001" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "same-path.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]
    client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}",
        json={"decision": "reuse"},
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (2, 1)
    assert [segment["id"] for segment in committed["network"]["pathSegments"]] == [
        saved["id"]
    ]


def test_reusing_existing_path_can_save_when_old_saved_duplicate_exists(app, client):
    with app.app_context():
        first_start = create_junction(13.0, 52.0)
        first_end = create_junction(13.002, 52.0)
        second_start = create_junction(13.0, 52.0)
        second_end = create_junction(13.002, 52.0)
        first = create_path_segment(
            first_start["id"],
            first_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        second = create_path_segment(
            second_start["id"],
            second_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.0" lon="13.0"/>
                        <trkpt lat="52.0" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "same-path.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()

    assert staged["canCommit"] is True
    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert {segment["id"] for segment in committed["network"]["pathSegments"]} == {
        first["id"],
        second["id"],
    }


def test_reverse_complete_overlap_can_be_reused(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.00001" lon="13.002"/>
                        <trkpt lat="51.99999" lon="13.001"/>
                        <trkpt lat="52.00001" lon="13.0"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "reverse.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]

    reused = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}",
        json={"decision": "reuse"},
    ).get_json()

    assert reused["canCommit"] is True
    assert reused["changeSummary"]["addedPathSegments"] == 0


def test_overlap_decision_validation_and_stale_session(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1"><trk><trkseg>
                      <trkpt lat="52.00001" lon="13.0"/>
                      <trkpt lat="52.00001" lon="13.002"/>
                    </trkseg></trk></gpx>"""
                ),
                "overlap.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]
    endpoint = (
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}"
    )

    assert client.put(endpoint, json={"decision": "maybe"}).status_code == 400
    assert client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/unknown",
        json={"decision": "reuse"},
    ).status_code == 400
    with app.app_context():
        create_junction(14.0, 53.0)
    assert client.put(endpoint, json={"decision": "reuse"}).status_code == 409
    assert client.put(
        f"/api/edit-sessions/unknown/imports/overlaps/{candidate['key']}",
        json={"decision": "reuse"},
    ).status_code == 404


def test_partial_overlap_reuse_commits_branch_junction_and_only_new_geometry(
    app,
    client,
):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        saved = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.00001" lon="13.0"/>
                        <trkpt lat="52.0" lon="13.0012"/>
                        <trkpt lat="52.0004" lon="13.0015"/>
                        <trkpt lat="52.001" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "branch.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]

    assert candidate["reviewType"] == "partial_section_reuse"
    reused = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}",
        json={"decision": "reuse"},
    ).get_json()
    assert reused["canCommit"] is True
    assert reused["changeSummary"]["replacedPathSegments"] == 1
    assert reused["changeSummary"]["addedPathSegments"] == 3

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert saved["id"] not in {
        segment["id"] for segment in committed["network"]["pathSegments"]
    }
    branch_junction = next(
        junction
        for junction in committed["network"]["junctions"]
        if 13.001 < junction["longitude"] < 13.0014
        and junction["latitude"] == pytest.approx(52.0)
    )
    assert sum(
        branch_junction["id"]
        in {segment["start_junction_id"], segment["end_junction_id"]}
        for segment in committed["network"]["pathSegments"]
    ) == 3
    assert database_counts(app) == (4, 3)


def test_overlap_boundary_adjustment_is_replayed_and_reset(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.000018" lon="13.0"/>
                        <trkpt lat="52.000018" lon="13.001"/>
                        <trkpt lat="52.000018" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "overlap.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]

    adjusted_response = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}/boundaries/start",
        json={"longitude": 13.0008, "latitude": 52.000018},
    )
    adjusted = adjusted_response.get_json()

    assert adjusted_response.status_code == 200
    adjusted_candidate = adjusted["import"]["overlapAnalysis"]["candidates"][0]
    assert adjusted_candidate["hasBoundaryAdjustment"] is True
    assert adjusted_candidate["startBoundary"]["type"] == "join"
    assert adjusted["changeSummary"]["addedPathSegments"] > 0

    reset_response = client.delete(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}/boundaries"
    )
    reset = reset_response.get_json()

    assert reset_response.status_code == 200
    assert reset["import"]["overlapAnalysis"]["candidates"][0].get(
        "hasBoundaryAdjustment"
    ) is not True
    assert reset["changeSummary"]["addedPathSegments"] == 0


def test_invalid_overlap_boundary_adjustment_is_rejected(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk><trkseg>
                        <trkpt lat="52.000018" lon="13.0"/>
                        <trkpt lat="52.000018" lon="13.001"/>
                        <trkpt lat="52.000018" lon="13.002"/>
                      </trkseg></trk>
                    </gpx>"""
                ),
                "overlap.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    candidate = staged["import"]["overlapAnalysis"]["candidates"][0]
    client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}/boundaries/end",
        json={"longitude": 13.0012, "latitude": 52.000018},
    )
    invalid = client.put(
        f"/api/edit-sessions/{session['token']}/imports/overlaps/{candidate['key']}/boundaries/start",
        json={"longitude": 13.0018, "latitude": 52.000018},
    )

    assert invalid.status_code == 409
    assert "start before end" in invalid.get_json()["error"]


def test_commit_replaces_saved_path_and_keeps_rtree_synchronized(app, client):
    with app.app_context():
        west = create_junction(13.404, 52.5205)
        east = create_junction(13.411, 52.5205)
        saved = create_path_segment(
            west["id"],
            east["id"],
            [[13.404, 52.5205], [13.411, 52.5205]],
            metadata={"surface": "gravel"},
        )
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(
            b"""<?xml version="1.0"?>
            <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
              <trk><trkseg>
                <trkpt lat="52.519" lon="13.407"/>
                <trkpt lat="52.522" lon="13.407"/>
              </trkseg></trk>
            </gpx>"""
        ), "crossing.gpx")},
        content_type="multipart/form-data",
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    network = response.get_json()["network"]

    assert response.status_code == 200
    assert saved["id"] not in {segment["id"] for segment in network["pathSegments"]}
    assert len(network["pathSegments"]) == 4
    assert len(network["junctions"]) == 5
    replacement_segments = [
        segment
        for segment in network["pathSegments"]
        if segment["source_filename"] is None
    ]
    assert replacement_segments
    assert all(
        segment["metadata"] == {"surface": "gravel"}
        for segment in replacement_segments
    )
    with app.app_context():
        indexed_ids = {
            row["path_segment_id"]
            for row in get_db()
            .execute("SELECT path_segment_id FROM path_segment_bounds")
            .fetchall()
        }
    assert indexed_ids == {segment["id"] for segment in network["pathSegments"]}


def test_duplicate_cleanup_reconnects_external_paths_and_commits(app, client):
    with app.app_context():
        retained_start = create_junction(13.0, 52.0)
        retained_end = create_junction(13.002, 52.0)
        duplicate_start = create_junction(13.0, 52.00002)
        duplicate_end = create_junction(13.002, 52.00002)
        branch_end = create_junction(13.0, 52.001)
        retained = create_path_segment(
            retained_start["id"],
            retained_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
            metadata={"surface": "asphalt"},
        )
        duplicate = create_path_segment(
            duplicate_start["id"],
            duplicate_end["id"],
            [[13.0, 52.00002], [13.002, 52.00002]],
            metadata={"surface": "gravel"},
        )
        branch = create_path_segment(
            duplicate_start["id"],
            branch_end["id"],
            [[13.0, 52.00002], [13.0, 52.001]],
            metadata={"kind": "spur"},
        )
    session = create_session(client)

    comparison_response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/compare",
        json={
            "firstPathSegmentId": retained["id"],
            "secondPathSegmentId": duplicate["id"],
            "retainedPathSegmentId": retained["id"],
        },
    )
    comparison = comparison_response.get_json()

    assert comparison_response.status_code == 200
    assert comparison["metadataConflict"] is True
    assert comparison["externalConnectionCount"] == 1
    assert comparison["maximumSeparationMetres"] < 3

    staged_response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups",
        json={
            "retainedPathSegmentId": retained["id"],
            "removedPathSegmentId": duplicate["id"],
        },
    )
    staged = staged_response.get_json()

    assert staged_response.status_code == 200
    assert staged["operations"][0]["type"] == "duplicate_cleanup"
    assert staged["changeSummary"]["deletedPathSegments"] == 1
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 1
    assert staged["changeSummary"]["deletedJunctions"] == 2
    replacement = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment.get("origin") == "duplicate_cleanup_rewire"
    )
    assert replacement["startJunctionId"] == retained_start["id"]
    assert replacement["endJunctionId"] == branch_end["id"]
    assert replacement["geometry"][0][:2] == [13.0, 52.0]
    assert replacement["metadata"] == {"kind": "spur"}

    committed_response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = committed_response.get_json()["network"]

    assert committed_response.status_code == 200
    assert duplicate["id"] not in {
        segment["id"] for segment in committed["pathSegments"]
    }
    assert branch["id"] not in {
        segment["id"] for segment in committed["pathSegments"]
    }
    assert retained["id"] in {
        segment["id"] for segment in committed["pathSegments"]
    }
    rewired = next(
        segment
        for segment in committed["pathSegments"]
        if segment["metadata"] == {"kind": "spur"}
    )
    assert rewired["start_junction_id"] == retained_start["id"]
    assert rewired["end_junction_id"] == branch_end["id"]
    assert database_counts(app) == (3, 2)


def test_area_cleanup_stages_duplicates_and_degree_two_junctions(app, client):
    with app.app_context():
        retained_start = create_junction(13.0, 52.0)
        retained_end = create_junction(13.002, 52.0)
        duplicate_start = create_junction(13.0, 52.00002)
        duplicate_end = create_junction(13.002, 52.00002)
        retained = create_path_segment(
            retained_start["id"],
            retained_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        duplicate = create_path_segment(
            duplicate_start["id"],
            duplicate_end["id"],
            [[13.0, 52.00002], [13.002, 52.00002]],
        )
        west = create_junction(13.01, 52.0)
        middle = create_junction(13.011, 52.0)
        east = create_junction(13.012, 52.0)
        create_path_segment(west["id"], middle["id"], [[13.01, 52.0], [13.011, 52.0]])
        create_path_segment(middle["id"], east["id"], [[13.011, 52.0], [13.012, 52.0]])
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/area",
        json={
            "bounds": {
                "minLongitude": 12.999,
                "minLatitude": 51.999,
                "maxLongitude": 13.013,
                "maxLatitude": 52.001,
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["areaCleanup"]["duplicateCleanupCount"] == 1
    assert staged["areaCleanup"]["junctionMergeCount"] == 1
    assert [operation["type"] for operation in staged["operations"]] == [
        "duplicate_cleanup",
        "merge_at_junction",
    ]
    assert duplicate["id"] not in {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }
    assert middle["id"] not in {
        junction["id"]
        for junction in staged["network"]["junctions"]
        if junction["state"] in {"saved", "added"}
    }
    assert retained["id"] in {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }


def test_area_cleanup_skips_protected_degree_two_junction(app, client):
    with app.app_context():
        west = create_junction(13.01, 52.0)
        middle = create_junction(13.011, 52.0, metadata={"protected": True})
        east = create_junction(13.012, 52.0)
        create_path_segment(west["id"], middle["id"], [[13.01, 52.0], [13.011, 52.0]])
        create_path_segment(middle["id"], east["id"], [[13.011, 52.0], [13.012, 52.0]])
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/area",
        json={
            "bounds": {
                "minLongitude": 13.0,
                "minLatitude": 51.999,
                "maxLongitude": 13.02,
                "maxLatitude": 52.001,
            }
        },
    )

    assert response.status_code == 400
    assert "No high-confidence duplicate paths" in response.get_json()["error"]


def test_area_cleanup_catches_wavy_parallel_duplicate(app, client):
    with app.app_context():
        retained_start = create_junction(13.0, 52.0)
        retained_end = create_junction(13.004, 52.0)
        duplicate_start = create_junction(13.0, 52.00009)
        duplicate_end = create_junction(13.004, 52.00009)
        retained = create_path_segment(
            retained_start["id"],
            retained_end["id"],
            [
                [13.0, 52.0],
                [13.001, 52.00012],
                [13.002, 52.0],
                [13.003, 52.00008],
                [13.004, 52.0],
            ],
        )
        duplicate = create_path_segment(
            duplicate_start["id"],
            duplicate_end["id"],
            [
                [13.0, 52.00009],
                [13.001, 52.0002],
                [13.002, 52.0001],
                [13.003, 52.00018],
                [13.004, 52.00009],
            ],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/area",
        json={
            "bounds": {
                "minLongitude": 12.999,
                "minLatitude": 51.999,
                "maxLongitude": 13.005,
                "maxLatitude": 52.001,
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["areaCleanup"]["duplicateCleanupCount"] == 1
    assert staged["operations"][0]["type"] == "duplicate_cleanup"
    active_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }
    deleted_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "deleted"
    }
    assert retained["id"] in active_ids
    assert duplicate["id"] in deleted_ids


def test_area_cleanup_deletes_stub_that_would_collapse_external_connection(
    app,
    client,
):
    with app.app_context():
        retained_start = create_junction(13.0, 52.0)
        retained_end = create_junction(13.002, 52.0)
        duplicate_start = create_junction(13.0, 52.00002)
        duplicate_end = create_junction(13.002, 52.00002)
        create_path_segment(
            retained_start["id"],
            retained_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        duplicate = create_path_segment(
            duplicate_start["id"],
            duplicate_end["id"],
            [[13.0, 52.00002], [13.002, 52.00002]],
        )
        connector = create_path_segment(
            duplicate_start["id"],
            retained_start["id"],
            [[13.0, 52.00002], [13.0, 52.0]],
        )
        west = create_junction(13.01, 52.0)
        middle = create_junction(13.011, 52.0)
        east = create_junction(13.012, 52.0)
        create_path_segment(west["id"], middle["id"], [[13.01, 52.0], [13.011, 52.0]])
        create_path_segment(middle["id"], east["id"], [[13.011, 52.0], [13.012, 52.0]])
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/area",
        json={
            "bounds": {
                "minLongitude": 12.999,
                "minLatitude": 51.999,
                "maxLongitude": 13.013,
                "maxLatitude": 52.001,
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["areaCleanup"]["duplicateCleanupCount"] == 1
    assert staged["areaCleanup"]["junctionMergeCount"] >= 1
    assert staged["operations"][0]["type"] == "duplicate_cleanup"
    deleted_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "deleted"
    }
    assert duplicate["id"] in deleted_ids
    assert connector["id"] in deleted_ids


def test_area_cleanup_deletes_short_leaf_stub_at_high_degree_junction(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        center = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        north = create_junction(13.001, 52.001)
        stub_end = create_junction(13.00105, 52.00002)
        west_segment = create_path_segment(
            west["id"],
            center["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        east_segment = create_path_segment(
            center["id"],
            east["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
        north_segment = create_path_segment(
            center["id"],
            north["id"],
            [[13.001, 52.0], [13.001, 52.001]],
        )
        stub = create_path_segment(
            center["id"],
            stub_end["id"],
            [[13.001, 52.0], [13.00105, 52.00002]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/duplicate-cleanups/area",
        json={
            "bounds": {
                "minLongitude": 13.0005,
                "minLatitude": 51.9995,
                "maxLongitude": 13.0015,
                "maxLatitude": 52.0005,
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["areaCleanup"]["stubDeleteCount"] >= 1
    assert "delete_path_segment" in {
        operation["type"] for operation in staged["operations"]
    }
    deleted_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "deleted"
    }
    active_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }
    assert stub["id"] in deleted_ids
    assert not {west_segment["id"], east_segment["id"], north_segment["id"]} & deleted_ids


def test_commit_auto_removes_new_duplicate_added_by_staged_edit(app, client):
    with app.app_context():
        shared_start = create_junction(13.0, 52.0)
        retained_end = create_junction(13.001, 52.0)
        moved_end = create_junction(13.002, 52.0)
        retained = create_path_segment(
            shared_start["id"],
            retained_end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        replaced = create_path_segment(
            shared_start["id"],
            moved_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    app.extensions["edit_sessions"][session["token"]]["operations"].append(
        {
            "type": "move_junction",
            "junctionId": moved_end["id"],
            "pathSegmentIds": [replaced["id"]],
            "replacementJunctionId": "moved-onto-retained-end",
            "replacementPathSegmentIds": ["duplicate-added-path"],
            "coordinate": [13.001, 52.0],
        }
    )

    staged_response = client.get(f"/api/edit-sessions/{session['token']}")
    staged = staged_response.get_json()

    assert staged_response.status_code == 200
    assert staged["canCommit"] is True
    assert staged["changeSummary"]["addedPathSegments"] == 0
    assert staged["skippedDuplicates"] == [
        {
            "pathSegmentId": "duplicate-added-path",
            "reason": "Duplicate staged geometry",
        }
    ]

    committed_response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = committed_response.get_json()

    assert committed_response.status_code == 200
    assert {segment["id"] for segment in committed["network"]["pathSegments"]} == {
        retained["id"]
    }
    assert database_counts(app) == (2, 1)


def test_second_import_is_rejected_until_first_is_undone(client, sample_gpx_bytes):
    session = create_session(client)
    endpoint = f"/api/edit-sessions/{session['token']}/imports"
    first = client.post(
        endpoint,
        data={"file": (io.BytesIO(sample_gpx_bytes), "first.gpx")},
        content_type="multipart/form-data",
    )
    second = client.post(
        endpoint,
        data={"file": (io.BytesIO(sample_gpx_bytes), "second.gpx")},
        content_type="multipart/form-data",
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert "already contains a GPX import" in second.get_json()["error"]


def test_replaying_import_after_undo_derives_same_staged_topology(
    client, sample_gpx_bytes
):
    session = create_session(client)
    endpoint = f"/api/edit-sessions/{session['token']}/imports"
    first = client.post(
        endpoint,
        data={"file": (io.BytesIO(sample_gpx_bytes), "route.gpx")},
        content_type="multipart/form-data",
    ).get_json()
    client.post(f"/api/edit-sessions/{session['token']}/undo")
    replayed = client.post(
        endpoint,
        data={"file": (io.BytesIO(sample_gpx_bytes), "route.gpx")},
        content_type="multipart/form-data",
    ).get_json()

    assert replayed["network"] == first["network"]
    assert replayed["changeSummary"] == first["changeSummary"]


def test_cancel_discards_session_without_database_changes(
    app, client, sample_gpx_bytes
):
    session = create_session(client)
    before = database_counts(app)
    client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    )

    response = client.delete(f"/api/edit-sessions/{session['token']}")

    assert response.status_code == 204
    assert database_counts(app) == before
    assert client.get(f"/api/edit-sessions/{session['token']}").status_code == 404


def test_session_reports_stale_revision_after_saved_network_changes(app, client):
    session = create_session(client)
    with app.app_context():
        create_junction(13.0, 52.0)

    response = client.get(f"/api/edit-sessions/{session['token']}")

    assert response.status_code == 200
    assert response.get_json()["isStale"] is True
    assert response.get_json()["canCommit"] is False


def test_stale_session_commit_is_rejected_without_applying_staged_changes(
    app, client, sample_gpx_bytes
):
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        create_junction(13.0, 52.0)

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")

    assert response.status_code == 409
    assert "changed" in response.get_json()["error"]
    assert database_counts(app) == (1, 0)


def test_forced_commit_failure_rolls_back_every_write(
    app, client, sample_gpx_bytes, monkeypatch
):
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    )

    def fail_insert(*_args, **_kwargs):
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(repository, "_insert_staged_path_segment", fail_insert)
    with pytest.raises(RuntimeError, match="forced commit failure"):
        client.post(f"/api/edit-sessions/{session['token']}/commit")

    assert database_counts(app) == (0, 0)
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM path_segment_bounds"
        ).fetchone()[0] == 0
    reloaded = client.get(f"/api/edit-sessions/{session['token']}").get_json()
    assert reloaded["canCommit"] is True
    assert len(reloaded["operations"]) == 1


def test_commit_without_staged_changes_is_rejected(client):
    session = create_session(client)

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")

    assert response.status_code == 409
    assert response.get_json()["error"] == "There are no staged changes to save."


def test_commit_preserves_loop_topology(client, fixture_path):
    session = create_session(client)
    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(fixture_path("loop.gpx").read_bytes()),
                "loop.gpx",
            )
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201

    committed = client.post(
        f"/api/edit-sessions/{session['token']}/commit"
    ).get_json()

    assert len(committed["network"]["junctions"]) == 1
    assert len(committed["network"]["pathSegments"]) == 1
    segment = committed["network"]["pathSegments"][0]
    assert segment["start_junction_id"] == segment["end_junction_id"]


def test_commit_of_exact_duplicate_does_not_insert_second_copy(
    app, client, sample_gpx_bytes
):
    with app.app_context():
        start = create_junction(13.405, 52.52, 30)
        end = create_junction(13.41, 52.521, 42)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.405, 52.52, 30], [13.41, 52.521, 42]],
        )
    session = create_session(client)
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "duplicate.gpx")},
        content_type="multipart/form-data",
    ).get_json()

    assert staged["changeSummary"]["addedPathSegments"] == 0
    response = client.post(f"/api/edit-sessions/{session['token']}/commit")

    assert response.status_code == 200
    assert database_counts(app) == (2, 1)


def test_import_validation_does_not_append_operation(client):
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(b"not xml"), "broken.gpx")},
        content_type="multipart/form-data",
    )
    reloaded = client.get(f"/api/edit-sessions/{session['token']}").get_json()

    assert response.status_code == 400
    assert reloaded["operations"] == []


def test_edit_session_endpoints_validate_missing_resources(client):
    assert client.get("/api/edit-sessions/unknown").status_code == 404
    assert client.post("/api/edit-sessions/unknown/undo").status_code == 404
    assert client.delete("/api/edit-sessions/unknown").status_code == 404
    assert client.post("/api/edit-sessions/unknown/commit").status_code == 404


def test_import_requires_one_gpx_file(client):
    session = create_session(client)
    response = client.post(f"/api/edit-sessions/{session['token']}/imports", data={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Choose one GPX file."


def test_import_rejects_non_gpx_file(client):
    session = create_session(client)
    response = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Only .gpx files are accepted."


def test_manual_split_stages_saved_path_and_undo_restores_it(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0, 10)
        end = create_junction(13.002, 52.0, 30)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0, 10], [13.001, 52.0, 20], [13.002, 52.0, 30]],
            source_filename="source.gpx",
            metadata={"surface": "gravel"},
        )
    session = create_session(client)
    before = database_counts(app)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/split",
        json={"longitude": 13.0015, "latitude": 52.0002},
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "split_path_segment"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 1,
        "addedPathSegments": 2,
        "replacedPathSegments": 1,
        "deletedJunctions": 0,
        "deletedPathSegments": 0,
        "operationCount": 1,
    }
    original = next(
        item
        for item in staged["network"]["pathSegments"]
        if item["id"] == segment["id"]
    )
    replacements = [
        item
        for item in staged["network"]["pathSegments"]
        if item["state"] == "added"
    ]
    split_junction = next(
        item
        for item in staged["network"]["junctions"]
        if item["state"] == "added"
    )
    assert original["state"] == "replaced"
    assert len(replacements) == 2
    assert split_junction["longitude"] == pytest.approx(13.0015)
    assert split_junction["latitude"] == pytest.approx(52.0)
    assert split_junction["elevation"] == pytest.approx(25)
    assert all(item["metadata"] == {"surface": "gravel"} for item in replacements)
    assert all(item["source_filename"] == "source.gpx" for item in replacements)
    assert replacements[0]["geometry"][-1] == replacements[1]["geometry"][0]
    assert database_counts(app) == before

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]
    assert database_counts(app) == before


def test_manual_split_near_endpoint_selects_existing_junction_without_operation(
    app, client
):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/split",
        json={"longitude": 13.00005, "latitude": 52.00001},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["operations"] == []
    assert payload["selectedObject"] == {
        "type": "junction",
        "id": start["id"],
        "reason": "existing_endpoint",
    }
    assert payload["network"] == session["network"]


def test_manual_split_can_split_an_added_import_path(client, sample_gpx_bytes):
    session = create_session(client)
    imported = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    ).get_json()
    added_segment = next(
        segment
        for segment in imported["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    first, last = added_segment["geometry"][0], added_segment["geometry"][-1]

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{added_segment['id']}/split",
        json={
            "longitude": (first[0] + last[0]) / 2,
            "latitude": (first[1] + last[1]) / 2,
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "import_trace",
        "split_path_segment",
    ]
    assert added_segment["id"] not in {
        segment["id"] for segment in staged["network"]["pathSegments"]
    }
    assert staged["changeSummary"]["addedJunctions"] == 3
    assert staged["changeSummary"]["addedPathSegments"] == 2
    assert staged["changeSummary"]["replacedPathSegments"] == 0


def test_manual_split_commit_persists_two_replacements(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/split",
        json={"longitude": 13.001, "latitude": 52.0001},
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (3, 2)
    assert segment["id"] not in {
        item["id"] for item in committed["network"]["pathSegments"]
    }
    assert len(committed["network"]["pathSegments"]) == 2


def test_delete_saved_path_stages_orphan_junction_and_undo_restores_network(
    app, client
):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        shared = create_junction(13.001, 52.0)
        end = create_junction(13.002, 52.0)
        deleted_segment = create_path_segment(
            start["id"],
            shared["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        retained_segment = create_path_segment(
            shared["id"],
            end["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    before = database_counts(app)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{deleted_segment['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "delete_path_segment"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 0,
        "addedPathSegments": 0,
        "replacedPathSegments": 0,
        "deletedJunctions": 1,
        "deletedPathSegments": 1,
        "operationCount": 1,
    }
    segment_states = {
        segment["id"]: segment["state"]
        for segment in staged["network"]["pathSegments"]
    }
    junction_states = {
        junction["id"]: junction["state"]
        for junction in staged["network"]["junctions"]
    }
    assert segment_states[deleted_segment["id"]] == "deleted"
    assert segment_states[retained_segment["id"]] == "saved"
    assert junction_states[start["id"]] == "deleted"
    assert junction_states[shared["id"]] == "saved"
    assert junction_states[end["id"]] == "saved"
    assert database_counts(app) == before

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]
    assert database_counts(app) == before


def test_delete_saved_path_auto_merges_degree_two_endpoint_junctions(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        first = create_junction(13.001, 52.0)
        north = create_junction(13.001, 52.001)
        second = create_junction(13.002, 52.0)
        east = create_junction(13.003, 52.0)
        south = create_junction(13.002, 51.999)
        west_segment = create_path_segment(
            west["id"],
            first["id"],
            [[13.0, 52.0], [13.001, 52.0]],
            metadata={"surface": "gravel"},
        )
        deleted_segment = create_path_segment(
            first["id"],
            second["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
        north_segment = create_path_segment(
            first["id"],
            north["id"],
            [[13.001, 52.0], [13.001, 52.001]],
            metadata={"surface": "gravel"},
        )
        east_segment = create_path_segment(
            second["id"],
            east["id"],
            [[13.002, 52.0], [13.003, 52.0]],
        )
        south_segment = create_path_segment(
            second["id"],
            south["id"],
            [[13.002, 52.0], [13.002, 51.999]],
        )
    session = create_session(client)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{deleted_segment['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "delete_path_segment"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 0,
        "addedPathSegments": 2,
        "replacedPathSegments": 4,
        "deletedJunctions": 2,
        "deletedPathSegments": 1,
        "operationCount": 1,
    }
    active_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }
    deleted_junction_ids = {
        junction["id"]
        for junction in staged["network"]["junctions"]
        if junction["state"] == "deleted"
    }
    merged = [
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    ]
    assert west_segment["id"] not in active_ids
    assert north_segment["id"] not in active_ids
    assert east_segment["id"] not in active_ids
    assert south_segment["id"] not in active_ids
    assert deleted_segment["id"] not in active_ids
    assert deleted_junction_ids == {first["id"], second["id"]}
    assert {
        (segment["startJunctionId"], segment["endJunctionId"])
        for segment in merged
    } == {
        (west["id"], north["id"]),
        (east["id"], south["id"]),
    }
    assert next(
        segment
        for segment in merged
        if segment["startJunctionId"] == west["id"]
    )["metadata"] == {"surface": "gravel"}

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]


def test_delete_saved_path_auto_merge_keeps_first_metadata_when_metadata_conflicts(
    app,
    client,
):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        first = create_junction(13.001, 52.0)
        north = create_junction(13.001, 52.001)
        second = create_junction(13.002, 52.0)
        east = create_junction(13.003, 52.0)
        west_segment = create_path_segment(
            west["id"],
            first["id"],
            [[13.0, 52.0], [13.001, 52.0]],
            metadata={"surface": "gravel"},
        )
        deleted_segment = create_path_segment(
            first["id"],
            second["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
        north_segment = create_path_segment(
            first["id"],
            north["id"],
            [[13.001, 52.0], [13.001, 52.001]],
            metadata={"surface": "asphalt"},
        )
        east_segment = create_path_segment(
            second["id"],
            east["id"],
            [[13.002, 52.0], [13.003, 52.0]],
        )
    session = create_session(client)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{deleted_segment['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["changeSummary"]["addedPathSegments"] == 1
    assert staged["changeSummary"]["replacedPathSegments"] == 2
    assert staged["changeSummary"]["deletedJunctions"] == 1
    assert staged["changeSummary"]["deletedPathSegments"] == 1
    active_ids = {
        segment["id"]
        for segment in staged["network"]["pathSegments"]
        if segment["state"] in {"saved", "added"}
    }
    assert west_segment["id"] not in active_ids
    assert north_segment["id"] not in active_ids
    assert east_segment["id"] in active_ids
    assert deleted_segment["id"] not in active_ids
    merged = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert (merged["startJunctionId"], merged["endJunctionId"]) == (
        west["id"],
        north["id"],
    )
    assert merged["metadata"] == west_segment["metadata"]


def test_edit_path_geometry_replaces_segment_and_preserves_endpoints(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)

    response = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/geometry",
        json={
            "geometry": [
                [13.0, 52.0],
                [13.001, 52.0002],
                [13.0015, 52.0001],
                [13.002, 52.0],
            ]
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "edit_path_geometry"
    ]
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 1
    replacement = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert replacement["origin"] == "geometry_edit"
    assert replacement["startJunctionId"] == start["id"]
    assert replacement["endJunctionId"] == end["id"]
    assert replacement["geometry"] == [
        [13.0, 52.0],
        [13.001, 52.0002],
        [13.0015, 52.0001],
        [13.002, 52.0],
    ]

    moved_endpoint = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{replacement['id']}/geometry",
        json={
            "geometry": [
                [13.0001, 52.0],
                [13.001, 52.0002],
                [13.002, 52.0],
            ]
        },
    )
    assert moved_endpoint.status_code == 400
    assert "start junction" in moved_endpoint.get_json()["error"]


def test_create_path_segment_between_existing_junctions_and_commit(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": start["id"],
            "endJunctionId": end["id"],
            "geometry": [
                [13.0, 52.0],
                [13.001, 52.0002],
                [13.002, 52.0],
            ],
        },
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert staged["operations"][0]["label"] == "Create path segment"
    assert staged["changeSummary"]["addedPathSegments"] == 1
    created = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert created["origin"] == "manual_create"
    assert created["startJunctionId"] == start["id"]
    assert created["endJunctionId"] == end["id"]
    assert created["metadata"] == {}

    commit_response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = commit_response.get_json()

    assert commit_response.status_code == 200
    assert database_counts(app) == (2, 1)
    assert committed["committedChangeSummary"]["addedPathSegments"] == 1


def test_create_path_segment_warns_about_duplicate_connection(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": end["id"],
            "endJunctionId": start["id"],
            "geometry": [
                [13.002, 52.0],
                [13.001, 52.0002],
                [13.0, 52.0],
            ],
        },
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert staged["createdPathSegment"]["duplicateConnectionCount"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 1


def test_create_path_segment_can_end_at_new_leaf_junction(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": start["id"],
            "endCoordinate": [13.001, 52.0002],
            "geometry": [
                [13.0, 52.0],
                [13.001, 52.0002],
            ],
        },
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert staged["createdPathSegment"]["createdEndJunction"] is True
    assert staged["changeSummary"]["addedJunctions"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 1
    created_segment = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    created_junction = next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["state"] == "added"
    )
    assert created_segment["startJunctionId"] == start["id"]
    assert created_segment["endJunctionId"] == created_junction["id"]
    assert created_segment["geometry"][-1][:2] == [
        created_junction["longitude"],
        created_junction["latitude"],
    ]

    commit_response = client.post(f"/api/edit-sessions/{session['token']}/commit")

    assert commit_response.status_code == 200
    assert database_counts(app) == (2, 1)


def test_create_path_segment_can_end_by_splitting_target_path(app, client):
    with app.app_context():
        branch_start = create_junction(13.0, 52.0)
        target_start = create_junction(13.001, 51.999)
        target_end = create_junction(13.001, 52.001)
        target = create_path_segment(
            target_start["id"],
            target_end["id"],
            [[13.001, 51.999], [13.001, 52.001]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": branch_start["id"],
            "targetPathSegmentId": target["id"],
            "endCoordinate": [13.001, 52.0],
            "geometry": [
                [13.0, 52.0],
                [13.001, 52.0],
            ],
        },
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert staged["createdPathSegment"]["splitTargetPath"] is True
    assert staged["createdPathSegment"]["createdEndJunction"] is True
    assert staged["changeSummary"]["addedJunctions"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 3
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    assert [operation["type"] for operation in staged["operations"]] == [
        "create_path_segment"
    ]
    split_junction_id = staged["createdPathSegment"]["endJunctionId"]
    added_segments = [
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    ]
    branch = next(
        segment
        for segment in added_segments
        if segment["origin"] == "manual_create"
    )
    split_pieces = [
        segment
        for segment in added_segments
        if segment["origin"] != "manual_create"
    ]

    assert branch["startJunctionId"] == branch_start["id"]
    assert branch["endJunctionId"] == split_junction_id
    assert len(split_pieces) == 2
    assert all(
        split_junction_id in {piece["startJunctionId"], piece["endJunctionId"]}
        for piece in split_pieces
    )

    undone = client.post(f"/api/edit-sessions/{session['token']}/undo").get_json()

    assert undone["operations"] == []
    assert undone["changeSummary"]["operationCount"] == 0
    assert database_counts(app) == (3, 1)


def test_create_path_segment_target_path_endpoint_reuses_existing_junction(app, client):
    with app.app_context():
        branch_start = create_junction(13.0, 52.0)
        target_start = create_junction(13.001, 52.0)
        target_end = create_junction(13.002, 52.0)
        target = create_path_segment(
            target_start["id"],
            target_end["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": branch_start["id"],
            "targetPathSegmentId": target["id"],
            "endCoordinate": [13.001, 52.0],
            "geometry": [
                [13.0, 52.0],
                [13.001, 52.0],
            ],
        },
    )
    staged = response.get_json()

    assert response.status_code == 201
    assert staged["createdPathSegment"]["splitTargetPath"] is False
    assert staged["createdPathSegment"]["createdEndJunction"] is False
    assert staged["createdPathSegment"]["endJunctionId"] == target_start["id"]
    assert staged["changeSummary"]["addedJunctions"] == 0
    assert staged["changeSummary"]["addedPathSegments"] == 1
    assert staged["changeSummary"]["replacedPathSegments"] == 0


def test_create_path_segment_rejects_missing_or_mismatched_endpoints(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
    session = create_session(client)

    same_endpoint = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": start["id"],
            "endJunctionId": start["id"],
            "geometry": [[13.0, 52.0], [13.0, 52.0]],
        },
    )
    mismatched_geometry = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments",
        json={
            "startJunctionId": start["id"],
            "endJunctionId": end["id"],
            "geometry": [[13.0001, 52.0], [13.002, 52.0]],
        },
    )

    assert same_endpoint.status_code == 400
    assert "different junctions" in same_endpoint.get_json()["error"]
    assert mismatched_geometry.status_code == 400
    assert "start junction" in mismatched_geometry.get_json()["error"]


def test_edit_path_metadata_replaces_segment_and_preserves_geometry(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
            metadata={"surface": "gravel"},
        )
    session = create_session(client)

    response = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/metadata",
        json={
            "metadata": {
                "preference": "avoid",
                "route_flags": [" scenic ", "muddy", "muddy"],
                "notes": "  winter connector  ",
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "edit_path_metadata"
    ]
    assert staged["operations"][0]["label"] == "Edit path metadata"
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 1
    original = next(
        candidate
        for candidate in staged["network"]["pathSegments"]
        if candidate["id"] == segment["id"]
    )
    replacement = next(
        candidate
        for candidate in staged["network"]["pathSegments"]
        if candidate["state"] == "added"
    )
    assert original["state"] == "replaced"
    assert replacement["origin"] == "metadata_edit"
    assert replacement["startJunctionId"] == start["id"]
    assert replacement["endJunctionId"] == end["id"]
    assert replacement["geometry"] == segment["geometry"]
    assert replacement["distance_m"] == segment["distance_m"]
    assert replacement["metadata"] == {
        "surface": "gravel",
        "preference": "avoid",
        "route_flags": ["muddy", "scenic"],
        "notes": "winter connector",
    }


def test_edit_path_metadata_rejects_invalid_values(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)

    bad_preference = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/metadata",
        json={"metadata": {"preference": "love_it"}},
    )
    bad_flags = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/metadata",
        json={"metadata": {"route_flags": ["muddy", ""]}},
    )

    assert bad_preference.status_code == 400
    assert "preference" in bad_preference.get_json()["error"]
    assert bad_flags.status_code == 400
    assert "Route flags" in bad_flags.get_json()["error"]


def test_edit_path_metadata_clears_neutral_preference(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
            metadata={"preference": "like", "surface": "gravel"},
        )
    session = create_session(client)

    response = client.put(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/metadata",
        json={
            "metadata": {
                "preference": "",
                "route_flags": [],
                "notes": "",
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    replacement = next(
        candidate
        for candidate in staged["network"]["pathSegments"]
        if candidate["state"] == "added"
    )
    assert replacement["metadata"] == {
        "surface": "gravel",
        "route_flags": [],
        "notes": "",
    }


def test_edit_junction_metadata_sets_place_type_and_commit_persists(app, client):
    with app.app_context():
        junction = create_junction(13.0, 52.0)
    session = create_session(client)

    response = client.put(
        f"/api/edit-sessions/{session['token']}/junctions/{junction['id']}/metadata",
        json={
            "metadata": {
                "protected": False,
                "place_type": "route_terminus",
                "name": "  Victoria  ",
                "notes": "  Route terminus  ",
            }
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["operations"][0]["type"] == "edit_junction_metadata"
    assert staged["operations"][0]["label"] == "Edit junction metadata"
    edited = next(
        candidate
        for candidate in staged["network"]["junctions"]
        if candidate["id"] == junction["id"]
    )
    assert edited["metadata"] == {
        "protected": True,
        "place_type": "route_terminus",
        "name": "Victoria",
        "notes": "Route terminus",
    }

    committed = client.post(f"/api/edit-sessions/{session['token']}/commit").get_json()
    assert committed["network"]["junctions"][0]["metadata"] == edited["metadata"]


def test_edit_junction_metadata_rejects_invalid_place_type(app, client):
    with app.app_context():
        junction = create_junction(13.0, 52.0)
    session = create_session(client)

    response = client.put(
        f"/api/edit-sessions/{session['token']}/junctions/{junction['id']}/metadata",
        json={"metadata": {"place_type": "airport"}},
    )

    assert response.status_code == 400
    assert "place type" in response.get_json()["error"]


def test_protected_junction_rejects_merge_cleanup(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        middle = create_junction(13.001, 52.0, metadata={"protected": True})
        east = create_junction(13.002, 52.0)
        create_path_segment(west["id"], middle["id"], [[13.0, 52.0], [13.001, 52.0]])
        create_path_segment(middle["id"], east["id"], [[13.001, 52.0], [13.002, 52.0]])
    session = create_session(client)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{middle['id']}"
    )

    assert response.status_code == 400
    assert "Protected junctions" in response.get_json()["error"]


def test_delete_saved_path_commit_removes_only_genuine_orphans(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        shared = create_junction(13.001, 52.0)
        end = create_junction(13.002, 52.0)
        deleted_segment = create_path_segment(
            start["id"],
            shared["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        retained_segment = create_path_segment(
            shared["id"],
            end["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{deleted_segment['id']}"
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (2, 1)
    assert {junction["id"] for junction in committed["network"]["junctions"]} == {
        shared["id"],
        end["id"],
    }
    assert {
        segment["id"] for segment in committed["network"]["pathSegments"]
    } == {retained_segment["id"]}
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM path_segment_bounds"
        ).fetchone()[0] == 1


def test_cancel_discards_staged_path_deletion(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.001, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
    session = create_session(client)
    client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}"
    )

    response = client.delete(f"/api/edit-sessions/{session['token']}")

    assert response.status_code == 204
    assert database_counts(app) == (2, 1)
    saved = client.get("/api/path-network").get_json()
    assert [item["id"] for item in saved["pathSegments"]] == [segment["id"]]


def test_delete_added_path_removes_added_orphans_without_database_delete(
    app, client, sample_gpx_bytes
):
    session = create_session(client)
    imported = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={"file": (io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")},
        content_type="multipart/form-data",
    ).get_json()
    added_segment = imported["network"]["pathSegments"][0]

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/{added_segment['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "import_trace",
        "delete_path_segment",
    ]
    assert staged["network"]["pathSegments"] == []
    assert staged["network"]["junctions"] == []
    assert staged["changeSummary"]["addedPathSegments"] == 0
    assert staged["changeSummary"]["addedJunctions"] == 0
    assert staged["changeSummary"]["deletedPathSegments"] == 0
    assert staged["changeSummary"]["deletedJunctions"] == 0
    assert database_counts(app) == (0, 0)

    committed = client.post(
        f"/api/edit-sessions/{session['token']}/commit"
    ).get_json()
    assert committed["committed"] is True
    assert database_counts(app) == (0, 0)


def test_orphan_cleanup_removes_unreferenced_added_junctions_without_candidate():
    junctions = [
        {
            "id": "import-crossing-junction",
            "longitude": 13.0,
            "latitude": 52.0,
            "elevation": None,
            "state": "added",
        }
    ]

    _derive_orphan_junctions(junctions, [], set())

    assert junctions == []


def test_delete_path_rejects_missing_or_already_removed_segment(app, client):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.001, 52.0)
        segment = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
    session = create_session(client)
    endpoint = (
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}"
    )

    assert client.delete(endpoint).status_code == 200
    assert client.delete(endpoint).status_code == 400
    assert client.delete(
        f"/api/edit-sessions/{session['token']}/path-segments/unknown"
    ).status_code == 400
    assert client.delete(
        f"/api/edit-sessions/unknown/path-segments/{segment['id']}"
    ).status_code == 404


@pytest.mark.parametrize(
    ("first_geometry", "second_geometry", "expected_geometry"),
    [
        (
            [[13.0, 52.0], [13.001, 52.0]],
            [[13.001, 52.0], [13.002, 52.0]],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
        ),
        (
            [[13.001, 52.0], [13.0, 52.0]],
            [[13.002, 52.0], [13.001, 52.0]],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
        ),
    ],
)
def test_merge_degree_two_junction_orients_geometry_and_undo_restores_network(
    app,
    client,
    first_geometry,
    second_geometry,
    expected_geometry,
):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        middle = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        first_start, first_end = (
            (west, middle)
            if first_geometry[0] == [13.0, 52.0]
            else (middle, west)
        )
        second_start, second_end = (
            (middle, east)
            if second_geometry[0] == [13.001, 52.0]
            else (east, middle)
        )
        first = create_path_segment(
            first_start["id"],
            first_end["id"],
            first_geometry,
            source_filename="walk.gpx",
            metadata={"surface": "gravel"},
        )
        second = create_path_segment(
            second_start["id"],
            second_end["id"],
            second_geometry,
            source_filename="walk.gpx",
            metadata={"surface": "gravel"},
        )
    session = create_session(client)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{middle['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "merge_at_junction"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 0,
        "addedPathSegments": 1,
        "replacedPathSegments": 2,
        "deletedJunctions": 1,
        "deletedPathSegments": 0,
        "operationCount": 1,
    }
    source_states = {
        segment["id"]: segment["state"]
        for segment in staged["network"]["pathSegments"]
        if segment["id"] in {first["id"], second["id"]}
    }
    assert source_states == {first["id"]: "replaced", second["id"]: "replaced"}
    merged = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert merged["geometry"] == expected_geometry
    assert {
        merged["startJunctionId"],
        merged["endJunctionId"],
    } == {west["id"], east["id"]}
    assert merged["metadata"] == {"surface": "gravel"}
    assert merged["source_filename"] == "walk.gpx"
    assert next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["id"] == middle["id"]
    )["state"] == "deleted"
    assert database_counts(app) == (3, 2)

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]
    assert database_counts(app) == (3, 2)


def test_merge_degree_two_junction_commit_and_cancel_preserve_graph(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        middle = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(
            west["id"],
            middle["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        create_path_segment(
            middle["id"],
            east["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
    cancelled_session = create_session(client)
    client.delete(
        f"/api/edit-sessions/{cancelled_session['token']}/junctions/{middle['id']}"
    )
    assert client.delete(
        f"/api/edit-sessions/{cancelled_session['token']}"
    ).status_code == 204
    assert database_counts(app) == (3, 2)

    session = create_session(client)
    client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{middle['id']}"
    )
    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (2, 1)
    assert {junction["id"] for junction in committed["network"]["junctions"]} == {
        west["id"],
        east["id"],
    }
    merged = committed["network"]["pathSegments"][0]
    assert merged["start_junction_id"] == west["id"]
    assert merged["end_junction_id"] == east["id"]
    assert merged["geometry"] == [
        [13.0, 52.0],
        [13.001, 52.0],
        [13.002, 52.0],
    ]


def test_merge_junctions_rewires_paths_and_removes_direct_segment(app, client):
    with app.app_context():
        source = create_junction(13.0, 52.0)
        target = create_junction(13.001, 52.0)
        branch = create_junction(13.0, 52.001)
        other = create_junction(13.002, 52.0)
        direct = create_path_segment(
            source["id"],
            target["id"],
            [[13.0, 52.0], [13.001, 52.0]],
            metadata={"kind": "direct"},
        )
        branch_segment = create_path_segment(
            source["id"],
            branch["id"],
            [[13.0, 52.0], [13.0, 52.001]],
            metadata={"kind": "branch"},
        )
        untouched = create_path_segment(
            target["id"],
            other["id"],
            [[13.001, 52.0], [13.002, 52.0]],
            metadata={"kind": "untouched"},
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{source['id']}/merge",
        json={"targetJunctionId": target["id"]},
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "merge_junctions"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 0,
        "addedPathSegments": 1,
        "replacedPathSegments": 1,
        "deletedJunctions": 1,
        "deletedPathSegments": 1,
        "operationCount": 1,
    }
    states = {
        segment["id"]: segment["state"]
        for segment in staged["network"]["pathSegments"]
        if segment["id"] in {direct["id"], branch_segment["id"], untouched["id"]}
    }
    assert states == {
        direct["id"]: "deleted",
        branch_segment["id"]: "replaced",
        untouched["id"]: "saved",
    }
    rewired = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert rewired["startJunctionId"] == target["id"]
    assert rewired["endJunctionId"] == branch["id"]
    assert rewired["geometry"] == [[13.001, 52.0], [13.0, 52.001]]
    assert rewired["metadata"] == {"kind": "branch"}
    assert next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["id"] == source["id"]
    )["state"] == "deleted"

    committed_response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = committed_response.get_json()

    assert committed_response.status_code == 200
    assert direct["id"] not in {
        segment["id"] for segment in committed["network"]["pathSegments"]
    }
    assert branch_segment["id"] not in {
        segment["id"] for segment in committed["network"]["pathSegments"]
    }
    assert source["id"] not in {
        junction["id"] for junction in committed["network"]["junctions"]
    }
    assert database_counts(app) == (3, 2)


def test_merge_can_remove_junction_added_by_manual_split(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        original = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
            metadata={"surface": "paved"},
        )
    session = create_session(client)
    split = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{original['id']}/split",
        json={"longitude": 13.001, "latitude": 52.0001},
    ).get_json()
    added_junction = next(
        junction
        for junction in split["network"]["junctions"]
        if junction["state"] == "added"
    )

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{added_junction['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "split_path_segment",
        "merge_at_junction",
    ]
    assert added_junction["id"] not in {
        junction["id"] for junction in staged["network"]["junctions"]
    }
    assert staged["changeSummary"]["addedJunctions"] == 0
    assert staged["changeSummary"]["addedPathSegments"] == 1
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    merged = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert merged["geometry"] == original["geometry"]


def test_merge_saved_junction_with_added_incident_paths_removes_saved_orphan(
    app, client
):
    with app.app_context():
        middle = create_junction(13.001, 52.0)
    session = create_session(client)
    imported = client.post(
        f"/api/edit-sessions/{session['token']}/imports",
        data={
            "file": (
                io.BytesIO(
                    b"""<?xml version="1.0"?>
                    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                      <trk>
                        <trkseg>
                          <trkpt lat="52.0" lon="13.0"/>
                          <trkpt lat="52.0" lon="13.001"/>
                        </trkseg>
                        <trkseg>
                          <trkpt lat="52.0" lon="13.001"/>
                          <trkpt lat="52.0" lon="13.002"/>
                        </trkseg>
                      </trk>
                    </gpx>"""
                ),
                "through-junction.gpx",
            )
        },
        content_type="multipart/form-data",
    ).get_json()
    assert sum(
        middle["id"]
        in {
            segment["startJunctionId"],
            segment["endJunctionId"],
        }
        for segment in imported["network"]["pathSegments"]
        if segment["state"] == "added"
    ) == 2

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{middle['id']}"
    )
    assert response.status_code == 200
    commit_response = client.post(
        f"/api/edit-sessions/{session['token']}/commit"
    )
    committed = commit_response.get_json()

    assert commit_response.status_code == 200
    assert database_counts(app) == (2, 1)
    assert middle["id"] not in {
        junction["id"] for junction in committed["network"]["junctions"]
    }


def test_merge_rejects_non_degree_two_and_loops(app, client):
    with app.app_context():
        isolated = create_junction(12.9, 52.0)
        loop_junction = create_junction(14.0, 53.0)
        other = create_junction(14.001, 53.0)
        create_path_segment(
            loop_junction["id"],
            loop_junction["id"],
            [[14.0, 53.0], [14.0005, 53.0005], [14.0, 53.0]],
        )
        create_path_segment(
            loop_junction["id"],
            other["id"],
            [[14.0, 53.0], [14.001, 53.0]],
        )
    session = create_session(client)

    degree_response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{isolated['id']}"
    )
    loop_response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{loop_junction['id']}"
    )

    assert degree_response.status_code == 400
    assert "degree is exactly two" in degree_response.get_json()["error"]
    assert loop_response.status_code == 400
    assert "Loop path segments" in loop_response.get_json()["error"]
    assert client.get(
        f"/api/edit-sessions/{session['token']}"
    ).get_json()["operations"] == []
    assert client.delete(
        f"/api/edit-sessions/unknown/junctions/{loop_junction['id']}"
    ).status_code == 404


def test_merge_with_incompatible_metadata_keeps_first_incident_metadata(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        middle = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        first = create_path_segment(
            west["id"],
            middle["id"],
            [[13.0, 52.0], [13.001, 52.0]],
            metadata={"surface": "gravel"},
        )
        second = create_path_segment(
            middle["id"],
            east["id"],
            [[13.001, 52.0], [13.002, 52.0]],
            metadata={"surface": "paved"},
        )
    session = create_session(client)

    response = client.delete(
        f"/api/edit-sessions/{session['token']}/junctions/{middle['id']}"
    )
    staged = response.get_json()

    assert response.status_code == 200
    merged = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert merged["metadata"] == first["metadata"]
    assert {
        segment["id"]: segment["state"]
        for segment in staged["network"]["pathSegments"]
        if segment["id"] in {first["id"], second["id"]}
    } == {first["id"]: "replaced", second["id"]: "replaced"}


def test_move_saved_junction_updates_every_incident_path_and_undo_restores(
    app, client
):
    with app.app_context():
        center = create_junction(13.001, 52.0, 25)
        west = create_junction(13.0, 52.0, 20)
        east = create_junction(13.002, 52.0, 30)
        north = create_junction(13.001, 52.001, 35)
        segments = [
            create_path_segment(
                west["id"],
                center["id"],
                [[13.0, 52.0, 20], [13.0005, 52.0, 22], [13.001, 52.0, 25]],
                source_filename="west.gpx",
                metadata={"surface": "gravel"},
            ),
            create_path_segment(
                center["id"],
                east["id"],
                [[13.001, 52.0, 25], [13.0015, 52.0, 27], [13.002, 52.0, 30]],
                source_filename="east.gpx",
                metadata={"surface": "paved"},
            ),
            create_path_segment(
                center["id"],
                north["id"],
                [[13.001, 52.0, 25], [13.001, 52.0005, 30], [13.001, 52.001, 35]],
                source_filename="north.gpx",
                metadata={"surface": "dirt"},
            ),
        ]
    session = create_session(client)
    before = database_counts(app)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{center['id']}/move",
        json={"longitude": 13.0011, "latitude": 52.0001},
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "move_junction"
    ]
    assert staged["changeSummary"] == {
        "addedJunctions": 1,
        "addedPathSegments": 3,
        "replacedPathSegments": 3,
        "deletedJunctions": 1,
        "deletedPathSegments": 0,
        "operationCount": 1,
    }
    assert next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["id"] == center["id"]
    )["state"] == "deleted"
    moved = next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["state"] == "added"
    )
    assert moved["longitude"] == pytest.approx(13.0011)
    assert moved["latitude"] == pytest.approx(52.0001)
    assert moved["elevation"] == 25

    replacements = [
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    ]
    assert len(replacements) == 3
    assert {
        segment["id"]: segment["state"]
        for segment in staged["network"]["pathSegments"]
        if segment["id"] in {source["id"] for source in segments}
    } == {source["id"]: "replaced" for source in segments}
    assert all(
        moved["id"]
        in {segment["startJunctionId"], segment["endJunctionId"]}
        for segment in replacements
    )
    moved_coordinate = [13.0011, 52.0001, 25.0]
    assert all(
        segment["geometry"][0] == moved_coordinate
        or segment["geometry"][-1] == moved_coordinate
        for segment in replacements
    )
    assert {segment["metadata"]["surface"] for segment in replacements} == {
        "gravel",
        "paved",
        "dirt",
    }
    assert database_counts(app) == before

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]
    assert database_counts(app) == before


def test_move_junction_commit_replaces_junction_and_paths_atomically(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        center = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        first = create_path_segment(
            west["id"],
            center["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
        second = create_path_segment(
            center["id"],
            east["id"],
            [[13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{center['id']}/move",
        json={"longitude": 13.001, "latitude": 52.0002},
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (3, 2)
    assert center["id"] not in {
        junction["id"] for junction in committed["network"]["junctions"]
    }
    assert {first["id"], second["id"]}.isdisjoint(
        segment["id"] for segment in committed["network"]["pathSegments"]
    )
    moved = next(
        junction
        for junction in committed["network"]["junctions"]
        if junction["longitude"] == pytest.approx(13.001)
        and junction["latitude"] == pytest.approx(52.0002)
    )
    assert all(
        moved["id"]
        in {segment["start_junction_id"], segment["end_junction_id"]}
        for segment in committed["network"]["pathSegments"]
    )
    assert all(
        segment["geometry"][0][:2] == [13.001, 52.0002]
        or segment["geometry"][-1][:2] == [13.001, 52.0002]
        for segment in committed["network"]["pathSegments"]
    )


def test_cancel_discards_staged_junction_move(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.001, 52.0)
        segment = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{west['id']}/move",
        json={"longitude": 12.9998, "latitude": 52.0001},
    )

    response = client.delete(f"/api/edit-sessions/{session['token']}")
    saved = client.get("/api/path-network").get_json()

    assert response.status_code == 204
    assert database_counts(app) == (2, 1)
    assert [junction["id"] for junction in saved["junctions"]] == [
        west["id"],
        east["id"],
    ]
    assert saved["pathSegments"][0]["id"] == segment["id"]
    assert saved["pathSegments"][0]["geometry"] == segment["geometry"]


def test_move_added_split_junction_replays_on_staged_geometry(app, client):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        original = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    split = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{original['id']}/split",
        json={"longitude": 13.001, "latitude": 52.0001},
    ).get_json()
    split_junction = next(
        junction
        for junction in split["network"]["junctions"]
        if junction["state"] == "added"
    )

    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{split_junction['id']}/move",
        json={"longitude": 13.0011, "latitude": 52.0002},
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert [operation["type"] for operation in staged["operations"]] == [
        "split_path_segment",
        "move_junction",
    ]
    assert split_junction["id"] not in {
        junction["id"] for junction in staged["network"]["junctions"]
    }
    assert staged["changeSummary"]["addedJunctions"] == 1
    assert staged["changeSummary"]["addedPathSegments"] == 2
    assert staged["changeSummary"]["replacedPathSegments"] == 1
    moved = next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["state"] == "added"
    )
    assert moved["longitude"] == pytest.approx(13.0011)
    assert moved["latitude"] == pytest.approx(52.0002)


def test_move_loop_junction_updates_both_geometry_endpoints(app, client):
    with app.app_context():
        junction = create_junction(13.0, 52.0)
        loop = create_path_segment(
            junction["id"],
            junction["id"],
            [[13.0, 52.0], [13.001, 52.001], [13.0, 52.0]],
        )
    session = create_session(client)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{junction['id']}/move",
        json={"longitude": 13.0001, "latitude": 52.0001},
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["id"] == loop["id"]
    )["state"] == "replaced"
    replacement = next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
    )
    assert replacement["startJunctionId"] == replacement["endJunctionId"]
    assert replacement["geometry"][0] == replacement["geometry"][-1]
    assert replacement["geometry"][0] == pytest.approx([13.0001, 52.0001])


def test_move_junction_rejects_invalid_or_unsafe_positions(app, client):
    with app.app_context():
        first = create_junction(13.0, 52.0)
        second = create_junction(13.001, 52.0)
        create_path_segment(
            first["id"],
            second["id"],
            [[13.0, 52.0], [13.001, 52.0]],
        )
    session = create_session(client)
    endpoint = (
        f"/api/edit-sessions/{session['token']}/junctions/{first['id']}/move"
    )

    assert client.post(endpoint, json={}).status_code == 400
    assert client.post(
        endpoint,
        json={"longitude": 13.0, "latitude": 52.0},
    ).status_code == 400
    close_response = client.post(
        endpoint,
        json={"longitude": 13.001, "latitude": 52.0},
    )
    assert close_response.status_code == 400
    assert "too close to another junction" in close_response.get_json()["error"]
    assert client.post(
        f"/api/edit-sessions/unknown/junctions/{first['id']}/move",
        json={"longitude": 13.002, "latitude": 52.0},
    ).status_code == 404
    assert client.post(
        f"/api/edit-sessions/{session['token']}/junctions/unknown/move",
        json={"longitude": 13.002, "latitude": 52.0},
    ).status_code == 400
    assert client.get(
        f"/api/edit-sessions/{session['token']}"
    ).get_json()["operations"] == []


def test_move_junction_onto_path_splits_target_and_creates_degree_three(
    app, client
):
    with app.app_context():
        branch_end = create_junction(13.0, 52.001)
        noisy_junction = create_junction(13.001, 52.0002)
        target_west = create_junction(13.0, 52.0)
        target_east = create_junction(13.002, 52.0)
        branch = create_path_segment(
            branch_end["id"],
            noisy_junction["id"],
            [[13.0, 52.001], [13.001, 52.0002]],
            metadata={"kind": "branch"},
        )
        target = create_path_segment(
            target_west["id"],
            target_east["id"],
            [[13.0, 52.0], [13.001, 52.0], [13.002, 52.0]],
            metadata={"kind": "main"},
        )
    session = create_session(client)
    before = database_counts(app)

    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{noisy_junction['id']}/move",
        json={
            "longitude": 13.0011,
            "latitude": 52.00008,
            "targetPathSegmentId": target["id"],
        },
    )
    staged = response.get_json()

    assert response.status_code == 200
    assert staged["changeSummary"] == {
        "addedJunctions": 1,
        "addedPathSegments": 3,
        "replacedPathSegments": 2,
        "deletedJunctions": 1,
        "deletedPathSegments": 0,
        "operationCount": 1,
    }
    moved = next(
        junction
        for junction in staged["network"]["junctions"]
        if junction["state"] == "added"
    )
    assert moved["longitude"] == pytest.approx(13.0011)
    assert moved["latitude"] == pytest.approx(52.0)
    active_incident = [
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["state"] == "added"
        and moved["id"]
        in {segment["startJunctionId"], segment["endJunctionId"]}
    ]
    assert len(active_incident) == 3
    assert sum(segment["origin"] == "junction_move" for segment in active_incident) == 1
    assert sum(
        segment["origin"] == "junction_move_split"
        for segment in active_incident
    ) == 2
    assert next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["id"] == branch["id"]
    )["state"] == "replaced"
    assert next(
        segment
        for segment in staged["network"]["pathSegments"]
        if segment["id"] == target["id"]
    )["state"] == "replaced"
    assert database_counts(app) == before

    undone = client.post(
        f"/api/edit-sessions/{session['token']}/undo"
    ).get_json()
    assert undone["network"] == session["network"]
    assert database_counts(app) == before


def test_move_junction_onto_path_commit_preserves_degree_three(app, client):
    with app.app_context():
        branch_end = create_junction(13.0, 52.001)
        noisy_junction = create_junction(13.001, 52.0002)
        target_west = create_junction(13.0, 52.0)
        target_east = create_junction(13.002, 52.0)
        create_path_segment(
            branch_end["id"],
            noisy_junction["id"],
            [[13.0, 52.001], [13.001, 52.0002]],
        )
        target = create_path_segment(
            target_west["id"],
            target_east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    client.post(
        f"/api/edit-sessions/{session['token']}/junctions/{noisy_junction['id']}/move",
        json={
            "longitude": 13.001,
            "latitude": 52.0001,
            "targetPathSegmentId": target["id"],
        },
    )

    response = client.post(f"/api/edit-sessions/{session['token']}/commit")
    committed = response.get_json()

    assert response.status_code == 200
    assert database_counts(app) == (4, 3)
    moved = next(
        junction
        for junction in committed["network"]["junctions"]
        if junction["longitude"] == pytest.approx(13.001)
        and junction["latitude"] == pytest.approx(52.0)
    )
    assert sum(
        moved["id"]
        in {segment["start_junction_id"], segment["end_junction_id"]}
        for segment in committed["network"]["pathSegments"]
    ) == 3


def test_move_junction_onto_path_rejects_endpoint_and_incident_target(app, client):
    with app.app_context():
        branch_end = create_junction(13.0, 52.001)
        noisy_junction = create_junction(13.001, 52.0002)
        target_west = create_junction(13.0, 52.0)
        target_east = create_junction(13.002, 52.0)
        branch = create_path_segment(
            branch_end["id"],
            noisy_junction["id"],
            [[13.0, 52.001], [13.001, 52.0002]],
        )
        target = create_path_segment(
            target_west["id"],
            target_east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
    session = create_session(client)
    endpoint = (
        f"/api/edit-sessions/{session['token']}/junctions/{noisy_junction['id']}/move"
    )

    near_endpoint = client.post(
        endpoint,
        json={
            "longitude": 13.00001,
            "latitude": 52.0,
            "targetPathSegmentId": target["id"],
        },
    )
    incident = client.post(
        endpoint,
        json={
            "longitude": 13.0005,
            "latitude": 52.0006,
            "targetPathSegmentId": branch["id"],
        },
    )

    assert near_endpoint.status_code == 400
    assert "more than ten metres" in near_endpoint.get_json()["error"]
    assert incident.status_code == 400
    assert "not already attached" in incident.get_json()["error"]
    assert client.get(
        f"/api/edit-sessions/{session['token']}"
    ).get_json()["operations"] == []
