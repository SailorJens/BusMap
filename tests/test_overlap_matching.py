import json

from path_network.dev import (
    analyze_overlap_case,
    overlap_case_matches_expectation,
)
from path_network.overlap_matching import analyze_trace_overlaps


def load_cases(fixture_path):
    return json.loads(
        fixture_path("overlap-cases.json").read_text()
    )["cases"]


def test_overlap_fixture_corpus_matches_expected_diagnostics(fixture_path):
    results = [
        (
            case["name"],
            overlap_case_matches_expectation(case, analyze_overlap_case(case)),
        )
        for case in load_cases(fixture_path)
    ]

    assert results
    assert all(matches for _name, matches in results), results


def test_overlap_analysis_is_deterministic(fixture_path):
    case = load_cases(fixture_path)[0]

    assert analyze_overlap_case(case) == analyze_overlap_case(case)


def test_negative_overlap_fixtures_do_not_qualify_for_automatic_reuse(fixture_path):
    risky_results = []
    for case in load_cases(fixture_path):
        expected = case.get("expected", {})
        is_negative = (
            expected.get("candidate") is False
            or expected.get("confidenceNot") == "high"
        )
        if not is_negative:
            continue
        analysis = analyze_overlap_case(case)
        risky_results.extend(
            (case["name"], candidate)
            for candidate in analysis["candidates"]
            if candidate["confidence"] == "high"
        )

    assert risky_results == []


def test_empty_saved_network_has_no_candidates():
    analysis = analyze_trace_overlaps(
        [
            {
                "id": 0,
                "sectionNumber": 1,
                "geometry": [[13.0, 52.0], [13.002, 52.0]],
            }
        ],
        [],
    )

    assert analysis["candidates"] == []
    assert analysis["summary"]["candidateCount"] == 0
    assert analysis["config"]["version"] == "diagnostics-v1"


def test_overlap_diagnostics_cli_reports_fixture_corpus(app, fixture_path):
    result = app.test_cli_runner().invoke(
        args=[
            "overlap-diagnostics",
            str(fixture_path("overlap-cases.json")),
            "--fail-on-mismatch",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "same path with gps noise" in result.output
    assert "10/10 cases matched." in result.output
