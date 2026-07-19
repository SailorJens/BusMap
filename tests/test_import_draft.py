import io

import pytest

from path_network.gpx import parse_gpx
from path_network.import_draft import (
    ImportDraftError,
    _automatic_overlap_decision,
    build_import_draft,
)
from path_network.repository import (
    create_junction,
    create_path_segment,
    list_junctions,
    list_path_segments,
)


def parse_fixture(fixture_path, name):
    with fixture_path(name).open("rb") as stream:
        return parse_gpx(stream, name)


def parsed_trace(*sections):
    return {
        "name": "Test trace",
        "filename": "test.gpx",
        "segments": [list(section) for section in sections],
        "stats": {
            "distanceMeters": 0,
            "elevationGainMeters": 0,
            "pointCount": sum(len(section) for section in sections),
        },
    }


def test_build_open_track_draft_is_deterministic(app, fixture_path):
    with app.app_context():
        parsed = parse_fixture(fixture_path, "morning-ride.gpx")
        first = build_import_draft(parsed)
        second = build_import_draft(parsed)

    assert first == second
    assert len(first["junctions"]) == 2
    assert len(first["pathSegments"]) == 1
    assert first["pathSegments"][0]["startJunctionId"] == "draft-junction-1"
    assert first["pathSegments"][0]["endJunctionId"] == "draft-junction-2"
    assert first["pathSegments"][0]["geometry"][0] == [13.405, 52.52, 30.0]


def test_loop_uses_one_endpoint_junction(app, fixture_path):
    with app.app_context():
        draft = build_import_draft(parse_fixture(fixture_path, "loop.gpx"))

    assert len(draft["junctions"]) == 1
    assert len(draft["pathSegments"]) == 1
    segment = draft["pathSegments"][0]
    assert segment["startJunctionId"] == segment["endJunctionId"]
    assert segment["geometry"][0] == segment["geometry"][-1]


def test_reverse_duplicate_section_is_skipped(app, fixture_path):
    with app.app_context():
        draft = build_import_draft(parse_fixture(fixture_path, "duplicate-sections.gpx"))

    assert len(draft["pathSegments"]) == 1
    assert len(draft["junctions"]) == 2
    assert draft["skippedDuplicates"] == [
        {"section": 2, "reason": "Exact duplicate geometry"}
    ]


def test_exact_saved_duplicate_is_skipped(app, sample_gpx_bytes):
    with app.app_context():
        parsed = parse_gpx(io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")
        start = create_junction(13.405, 52.52, 30)
        end = create_junction(13.41, 52.521, 42)
        create_path_segment(start["id"], end["id"], parsed["segments"][0])

        draft = build_import_draft(parsed)

    assert draft["pathSegments"] == []
    assert draft["junctions"] == []
    assert draft["skippedDuplicates"][0]["reason"] == "Exact duplicate geometry"


def test_crossing_saved_path_splits_both_geometries_without_writing(app):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        saved = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        before_junctions = list_junctions()
        before_segments = list_path_segments()

        draft = build_import_draft(
            parsed_trace([[13.001, 51.999], [13.001, 52.001]])
        )

        assert list_junctions() == before_junctions
        assert list_path_segments() == before_segments

    assert draft["replacedPathSegmentIds"] == [saved["id"]]
    assert len(draft["pathSegments"]) == 4
    crossing = next(
        junction
        for junction in draft["junctions"]
        if abs(junction["longitude"] - 13.001) < 1e-8
        and abs(junction["latitude"] - 52.0) < 1e-8
    )
    incident = [
        segment
        for segment in draft["pathSegments"]
        if crossing["id"] in (segment["startJunctionId"], segment["endJunctionId"])
    ]
    assert len(incident) == 4


def test_non_intersecting_saved_network_allows_draft(app, sample_gpx_bytes):
    with app.app_context():
        start = create_junction(11.0, 50.0)
        end = create_junction(11.1, 50.1)
        create_path_segment(start["id"], end["id"], [[11.0, 50.0], [11.1, 50.1]])

        parsed = parse_gpx(io.BytesIO(sample_gpx_bytes), "morning-ride.gpx")
        draft = build_import_draft(parsed)

    assert len(draft["pathSegments"]) == 1
    assert len(draft["junctions"]) == 2


def test_self_crossing_splits_one_track_section_at_shared_junction(app):
    with app.app_context():
        draft = build_import_draft(
            parsed_trace(
                [
                    [13.0, 52.0],
                    [13.002, 52.002],
                    [13.0, 52.002],
                    [13.002, 52.0],
                ]
            )
        )

    assert len(draft["pathSegments"]) == 3
    crossing = next(
        junction
        for junction in draft["junctions"]
        if abs(junction["longitude"] - 13.001) < 1e-8
        and abs(junction["latitude"] - 52.001) < 1e-8
    )
    assert sum(
        (segment["startJunctionId"] == crossing["id"])
        + (segment["endJunctionId"] == crossing["id"])
        for segment in draft["pathSegments"]
    ) == 4


def test_crossing_between_track_sections_splits_every_participant(app):
    with app.app_context():
        draft = build_import_draft(
            parsed_trace(
                [[13.0, 52.0], [13.002, 52.002]],
                [[13.0, 52.002], [13.002, 52.0]],
            )
        )

    assert len(draft["pathSegments"]) == 4
    assert len(draft["junctions"]) == 5


def test_nearby_parallel_medium_overlap_reuses_saved_path(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.004, 52.0)
        saved = create_path_segment(start["id"], end["id"], [[13.0, 52.0], [13.004, 52.0]])

        draft = build_import_draft(
            parsed_trace(
                [
                    [12.999, 52.001],
                    [13.001, 52.00005],
                    [13.003, 52.00005],
                    [13.005, 52.001],
                ]
            )
        )

    candidate = draft["overlapAnalysis"]["candidates"][0]
    assert candidate["confidence"] == "medium"
    assert candidate["decision"] == "reuse"
    assert candidate["decisionSource"] == "automatic"
    assert draft["replacedPathSegmentIds"] == [saved["id"]]
    assert len([segment for segment in draft["pathSegments"] if segment["origin"] == "import"]) == 2


def test_complete_overlap_is_automatically_reused_and_can_be_overridden(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )

        automatic = build_import_draft(
            parsed_trace(
                [[13.0, 52.000018], [13.001, 52.000018], [13.002, 52.000018]]
            )
        )

        candidate = automatic["overlapAnalysis"]["candidates"][0]
        reused = build_import_draft(
            parsed_trace(
                [[13.0, 52.000018], [13.001, 52.000018], [13.002, 52.000018]]
            ),
            overlap_decisions={candidate["key"]: "reuse"},
        )
        kept = build_import_draft(
            parsed_trace(
                [[13.0, 52.000018], [13.001, 52.000018], [13.002, 52.000018]]
            ),
            overlap_decisions={candidate["key"]: "keep"},
        )

    assert candidate["reviewType"] == "complete_section_reuse"
    assert candidate["decision"] == "reuse"
    assert candidate["decisionSource"] == "automatic"
    assert automatic["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert automatic["overlapAnalysis"]["summary"]["automaticReuseCount"] == 1
    assert automatic["overlapAnalysis"]["summary"]["newDistanceMetres"] == 0
    assert automatic["pathSegments"] == []
    assert reused["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert reused["overlapAnalysis"]["summary"]["reusedDistanceMetres"] > 0
    assert reused["overlapAnalysis"]["summary"]["newDistanceMetres"] == 0
    assert reused["pathSegments"] == []
    assert reused["junctions"] == []
    assert kept["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert kept["overlapAnalysis"]["summary"]["reusedDistanceMetres"] == 0
    assert len(kept["pathSegments"]) == 1
    assert kept["pathSegments"][0]["origin"] == "import"


def test_adjusted_overlap_boundary_rederives_partial_topology(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        saved = create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        parsed = parsed_trace(
            [[13.0, 52.000018], [13.001, 52.000018], [13.002, 52.000018]]
        )
        automatic = build_import_draft(parsed)
        candidate = automatic["overlapAnalysis"]["candidates"][0]
        adjusted = build_import_draft(
            parsed,
            overlap_decisions={candidate["key"]: "reuse"},
            overlap_adjustments={
                candidate["key"]: [
                    {
                        "boundary": "start",
                        "longitude": 13.0008,
                        "latitude": 52.000018,
                    }
                ]
            },
        )

    adjusted_candidate = adjusted["overlapAnalysis"]["candidates"][0]
    assert adjusted_candidate["hasBoundaryAdjustment"] is True
    assert adjusted_candidate["startBoundary"]["type"] == "join"
    assert adjusted["replacedPathSegmentIds"] == [saved["id"]]
    imports = [
        segment for segment in adjusted["pathSegments"] if segment["origin"] == "import"
    ]
    replacements = [
        segment for segment in adjusted["pathSegments"] if segment["origin"] == "replacement"
    ]
    assert len(imports) == 0
    assert len(replacements) == 2
    assert replacements[0]["geometry"][-1][1] == pytest.approx(52.0)


def test_adjusted_overlap_boundaries_must_remain_ordered(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        parsed = parsed_trace(
            [[13.0, 52.000018], [13.001, 52.000018], [13.002, 52.000018]]
        )
        candidate = build_import_draft(parsed)["overlapAnalysis"]["candidates"][0]
        with pytest.raises(ImportDraftError, match="start before end"):
            build_import_draft(
                parsed,
                overlap_decisions={candidate["key"]: "reuse"},
                overlap_adjustments={
                    candidate["key"]: [
                        {
                            "boundary": "start",
                            "longitude": 13.0018,
                            "latitude": 52.000018,
                        },
                        {
                            "boundary": "end",
                            "longitude": 13.0012,
                            "latitude": 52.000018,
                        },
                    ]
                },
            )


def test_partial_overlap_automatically_reuses_saved_path_and_keeps_new_branch(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(
            start["id"],
            end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        automatic = build_import_draft(
            parsed_trace(
                [
                    [13.0, 52.00001],
                    [13.0012, 52.0],
                    [13.0015, 52.0004],
                    [13.002, 52.001],
                ]
            )
        )
        candidate = automatic["overlapAnalysis"]["candidates"][0]
        reused = build_import_draft(
            parsed_trace(
                [
                    [13.0, 52.00001],
                    [13.0012, 52.0],
                    [13.0015, 52.0004],
                    [13.002, 52.001],
                ]
            ),
            overlap_decisions={candidate["key"]: "reuse"},
        )
        kept = build_import_draft(
            parsed_trace(
                [
                    [13.0, 52.00001],
                    [13.0012, 52.0],
                    [13.0015, 52.0004],
                    [13.002, 52.001],
                ]
            ),
            overlap_decisions={candidate["key"]: "keep"},
        )

    assert candidate["reviewType"] == "partial_section_reuse"
    assert candidate["startBoundary"]["type"] == "section_endpoint"
    assert candidate["endBoundary"]["type"] == "branch"
    assert candidate["decision"] == "reuse"
    assert candidate["decisionSource"] == "automatic"
    assert automatic["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert reused["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert reused["overlapAnalysis"]["summary"]["reusedDistanceMetres"] > 0
    imports = [
        segment
        for segment in reused["pathSegments"]
        if segment["origin"] == "import"
    ]
    replacements = [
        segment
        for segment in reused["pathSegments"]
        if segment["origin"] == "replacement"
    ]
    assert len(imports) == 1
    assert len(replacements) == 2
    branch_junction_id = imports[0]["startJunctionId"]
    assert sum(
        branch_junction_id
        in {segment["startJunctionId"], segment["endJunctionId"]}
        for segment in reused["pathSegments"]
    ) == 3
    assert imports[0]["geometry"][0][1] == pytest.approx(52.0)
    assert kept["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert kept["overlapAnalysis"]["summary"]["reusedDistanceMetres"] == 0
    assert automatic["overlapAnalysis"]["summary"]["reusedDistanceMetres"] > 0


def test_multiple_overlap_intervals_reuse_imports_only_new_detour(app):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.006, 52.0)
        saved = create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.006, 52.0]],
        )
        parsed = parsed_trace(
            [
                [13.0, 52.0],
                [13.002, 52.0],
                [13.003, 52.001],
                [13.004, 52.0],
                [13.006, 52.0],
            ]
        )
        reused = build_import_draft(parsed)
        candidates = reused["overlapAnalysis"]["candidates"]

    assert len(candidates) == 2
    assert {
        candidate["reviewType"]
        for candidate in candidates
    } == {"partial_section_reuse"}
    assert reused["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert {candidate["decision"] for candidate in candidates} == {"reuse"}
    assert {candidate["decisionSource"] for candidate in candidates} == {"automatic"}
    assert reused["replacedPathSegmentIds"] == [saved["id"]]
    replacements = [
        segment for segment in reused["pathSegments"] if segment["origin"] == "replacement"
    ]
    imports = [
        segment for segment in reused["pathSegments"] if segment["origin"] == "import"
    ]
    assert len(replacements) == 3
    assert len(imports) == 1
    assert imports[0]["geometry"][0][1] == pytest.approx(52.0)
    assert imports[0]["geometry"][-1][1] == pytest.approx(52.0)
    assert any(point[1] == pytest.approx(52.001) for point in imports[0]["geometry"])


def test_connected_saved_chain_can_be_reused_as_one_candidate(app):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        center = create_junction(13.002, 52.0)
        east = create_junction(13.004, 52.0)
        first = create_path_segment(
            west["id"],
            center["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        second = create_path_segment(
            center["id"],
            east["id"],
            [[13.002, 52.0], [13.004, 52.0]],
        )
        parsed = parsed_trace(
            [
                [13.0, 52.00001],
                [13.001, 51.99999],
                [13.002, 52.00001],
                [13.003, 51.99999],
                [13.004, 52.00001],
            ]
        )
        automatic = build_import_draft(parsed)
        candidate = automatic["overlapAnalysis"]["candidates"][0]
        reused = build_import_draft(
            parsed,
            overlap_decisions={candidate["key"]: "reuse"},
        )

    assert candidate["reviewType"] == "complete_section_reuse"
    assert candidate["savedChainContinuous"] is True
    assert candidate["savedPathSegmentIds"] == [first["id"], second["id"]]
    assert automatic["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert automatic["overlapAnalysis"]["summary"]["automaticReuseCount"] == 1
    assert automatic["pathSegments"] == []
    assert reused["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert reused["pathSegments"] == []
    assert reused["junctions"] == []


def test_overlap_decisions_reject_unknown_candidate_key(app):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        with pytest.raises(ImportDraftError, match="reviewable candidate"):
            build_import_draft(
                parsed_trace([[13.0, 52.0], [13.002, 52.0]]),
                overlap_decisions={"missing": "reuse"},
            )


def test_automatic_overlap_decision_reuses_high_and_medium_keeps_low():
    base_candidate = {
        "reviewType": "complete_section_reuse",
        "confidence": "high",
    }

    assert _automatic_overlap_decision(base_candidate) == "reuse"
    assert _automatic_overlap_decision(
        {**base_candidate, "confidence": "medium"},
    ) == "reuse"
    assert _automatic_overlap_decision(
        {**base_candidate, "confidence": "low"},
    ) == "keep"


def test_separate_gpx_sections_keep_separate_overlap_reviews(app):
    with app.app_context():
        first_start = create_junction(13.0, 52.0)
        first_end = create_junction(13.002, 52.0)
        second_start = create_junction(13.0, 52.001)
        second_end = create_junction(13.002, 52.001)
        create_path_segment(
            first_start["id"],
            first_end["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )
        create_path_segment(
            second_start["id"],
            second_end["id"],
            [[13.0, 52.001], [13.002, 52.001]],
        )
        draft = build_import_draft(
            parsed_trace(
                [[13.0, 52.00001], [13.002, 52.00001]],
                [[13.0, 52.00101], [13.002, 52.00101]],
            )
        )

    assert draft["overlapAnalysis"]["summary"]["reviewCandidateCount"] == 2
    assert {
        candidate["sectionNumber"]
        for candidate in draft["overlapAnalysis"]["candidates"]
    } == {1, 2}
    assert draft["overlapAnalysis"]["hasUnresolvedOverlaps"] is False
    assert draft["overlapAnalysis"]["summary"]["automaticReuseCount"] == 2


def test_middle_overlap_reuse_imports_prefix_and_suffix_only(app):
    with app.app_context():
        west = create_junction(13.001, 52.0)
        east = create_junction(13.003, 52.0)
        create_path_segment(
            west["id"],
            east["id"],
            [[13.001, 52.0], [13.003, 52.0]],
        )
        parsed = parsed_trace(
            [
                [13.0, 51.9995],
                [13.0011, 52.0],
                [13.0029, 52.0],
                [13.004, 52.0005],
            ]
        )
        unresolved = build_import_draft(parsed)
        candidate = unresolved["overlapAnalysis"]["candidates"][0]
        reused = build_import_draft(
            parsed,
            overlap_decisions={candidate["key"]: "reuse"},
        )

    assert candidate["reviewType"] == "partial_section_reuse"
    assert candidate["startBoundary"]["type"] == "join"
    assert candidate["endBoundary"]["type"] == "branch"
    imports = [
        segment
        for segment in reused["pathSegments"]
        if segment["origin"] == "import"
    ]
    assert len(imports) == 2
    assert reused["overlapAnalysis"]["summary"]["reusedDistanceMetres"] > 100


def test_trace_endpoint_within_ten_metres_connects_to_saved_path(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        saved = create_path_segment(
            start["id"], end["id"], [[13.0, 52.0], [13.002, 52.0]]
        )

        draft = build_import_draft(
            parsed_trace([[13.001, 52.00005], [13.001, 52.001]])
        )

    assert draft["replacedPathSegmentIds"] == [saved["id"]]
    assert len([segment for segment in draft["pathSegments"] if segment["origin"] == "replacement"]) == 2


def test_near_endpoint_crossing_does_not_create_tiny_connector_or_hidden_junction(
    app,
):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(
            west["id"],
            east["id"],
            [[13.0, 52.0], [13.002, 52.0]],
        )

        draft = build_import_draft(
            parsed_trace(
                [
                    [13.001, 51.99995],
                    [13.001, 52.001],
                ]
            )
        )

    imported = [
        segment
        for segment in draft["pathSegments"]
        if segment["origin"] == "import"
    ]
    crossing_junctions = [
        junction
        for junction in draft["junctions"]
        if abs(junction["longitude"] - 13.001) < 1e-8
        and abs(junction["latitude"] - 52.0) < 1e-8
    ]
    assert len(crossing_junctions) == 1
    assert len(imported) == 1
    assert imported[0]["distance_m"] > 100
    assert imported[0]["geometry"][0][:2] == pytest.approx([13.001, 52.0])


def test_trace_endpoint_outside_ten_metres_does_not_connect(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.002, 52.0)
        create_path_segment(start["id"], end["id"], [[13.0, 52.0], [13.002, 52.0]])

        draft = build_import_draft(
            parsed_trace([[13.001, 52.0002], [13.001, 52.001]])
        )

    assert draft["replacedPathSegmentIds"] == []
    assert len(draft["pathSegments"]) == 1


def test_existing_junction_within_ten_metres_splits_uploaded_trace(app):
    with app.app_context():
        west = create_junction(13.0, 52.0)
        centre = create_junction(13.001, 52.0)
        east = create_junction(13.002, 52.0)
        create_path_segment(west["id"], centre["id"], [[13.0, 52.0], [13.001, 52.0]])
        create_path_segment(centre["id"], east["id"], [[13.001, 52.0], [13.002, 52.0]])

        draft = build_import_draft(
            parsed_trace([[13.00105, 51.999], [13.00105, 52.001]])
        )

    snapped = next(
        junction
        for junction in draft["junctions"]
        if junction.get("existingJunctionId") == centre["id"]
    )
    imported = [segment for segment in draft["pathSegments"] if segment["origin"] == "import"]
    assert len(imported) == 2
    assert sum(
        snapped["id"] in (segment["startJunctionId"], segment["endJunctionId"])
        for segment in imported
    ) == 2


def test_two_crossings_split_one_saved_path_in_order(app):
    with app.app_context():
        start = create_junction(13.0, 52.0)
        end = create_junction(13.004, 52.0)
        saved = create_path_segment(
            start["id"], end["id"], [[13.0, 52.0], [13.004, 52.0]]
        )

        draft = build_import_draft(
            parsed_trace(
                [[13.001, 51.999], [13.001, 52.001]],
                [[13.003, 51.999], [13.003, 52.001]],
            )
        )

    replacements = [
        segment for segment in draft["pathSegments"] if segment["origin"] == "replacement"
    ]
    assert draft["replacedPathSegmentIds"] == [saved["id"]]
    assert len(replacements) == 3
    assert [segment["geometry"][0][0] for segment in replacements] == [
        13.0,
        13.001,
        13.003,
    ]


def test_nearby_crossings_are_clustered_into_one_junction(app):
    with app.app_context():
        first_start = create_junction(13.0, 52.0)
        first_end = create_junction(13.002, 52.0)
        second_start = create_junction(13.0, 52.00005)
        second_end = create_junction(13.002, 52.00005)
        create_path_segment(
            first_start["id"], first_end["id"], [[13.0, 52.0], [13.002, 52.0]]
        )
        create_path_segment(
            second_start["id"],
            second_end["id"],
            [[13.0, 52.00005], [13.002, 52.00005]],
        )

        draft = build_import_draft(
            parsed_trace([[13.001, 51.999], [13.001, 52.001]])
        )

    imported = [segment for segment in draft["pathSegments"] if segment["origin"] == "import"]
    crossing_junctions = [
        junction
        for junction in draft["junctions"]
        if 51.9999 < junction["latitude"] < 52.0002
    ]
    assert len(crossing_junctions) == 1
    assert len(imported) == 2


def test_self_crossing_loop_remains_valid_after_splitting(app):
    with app.app_context():
        draft = build_import_draft(
            parsed_trace(
                [
                    [13.0, 52.0],
                    [13.002, 52.002],
                    [13.0, 52.002],
                    [13.002, 52.0],
                    [13.0, 52.0],
                ]
            )
        )

    imported = [segment for segment in draft["pathSegments"] if segment["origin"] == "import"]
    assert len(imported) == 3
    assert any(
        segment["startJunctionId"] == segment["endJunctionId"]
        for segment in imported
    )


def test_saved_loop_can_be_split_at_multiple_crossings(app):
    with app.app_context():
        junction = create_junction(13.0, 52.0)
        saved_loop = create_path_segment(
            junction["id"],
            junction["id"],
            [
                [13.0, 52.0],
                [13.002, 52.0],
                [13.002, 52.002],
                [13.0, 52.002],
                [13.0, 52.0],
            ],
        )

        draft = build_import_draft(
            parsed_trace([[12.999, 52.001], [13.003, 52.001]])
        )

    replacements = [
        segment for segment in draft["pathSegments"] if segment["origin"] == "replacement"
    ]
    assert draft["replacedPathSegmentIds"] == [saved_loop["id"]]
    assert len(replacements) == 3


def test_draft_conversion_does_not_write_to_database(app, fixture_path):
    with app.app_context():
        build_import_draft(parse_fixture(fixture_path, "morning-ride.gpx"))

        assert list_junctions() == []
        assert list_path_segments() == []
