from __future__ import annotations

import json
from pathlib import Path

import click
from flask import Flask
from flask.cli import with_appcontext

from path_network.overlap_matching import analyze_trace_overlaps
from path_network.repository import (
    create_junction,
    create_path_segment,
    list_path_segments,
)


def seed_sample_network() -> dict:
    if list_path_segments():
        raise click.ClickException("The path network is not empty.")

    west = create_junction(13.3777, 52.5163, 35)
    center = create_junction(13.3904, 52.5208, 37)
    east = create_junction(13.4050, 52.5200, 32)

    first = create_path_segment(
        west["id"],
        center["id"],
        [
            [west["longitude"], west["latitude"], west["elevation"]],
            [13.3835, 52.5188, 36],
            [center["longitude"], center["latitude"], center["elevation"]],
        ],
        source_filename="sample-network.gpx",
    )
    second = create_path_segment(
        center["id"],
        east["id"],
        [
            [center["longitude"], center["latitude"], center["elevation"]],
            [13.3975, 52.5215, 34],
            [east["longitude"], east["latitude"], east["elevation"]],
        ],
        source_filename="sample-network.gpx",
    )
    return {"junctions": [west, center, east], "pathSegments": [first, second]}


@click.command("seed-network")
@with_appcontext
def seed_network_command() -> None:
    result = seed_sample_network()
    click.echo(
        f"Inserted {len(result['junctions'])} junctions and "
        f"{len(result['pathSegments'])} path segments."
    )


def analyze_overlap_case(case: dict) -> dict:
    sections = [
        {
            "id": index,
            "sectionNumber": index + 1,
            "geometry": geometry,
        }
        for index, geometry in enumerate(case["uploadedSections"])
    ]
    return analyze_trace_overlaps(sections, case["savedPathSegments"])


def overlap_case_matches_expectation(case: dict, analysis: dict) -> bool:
    expected = case.get("expected") or {}
    candidates = analysis["candidates"]
    if expected.get("candidate") is not None:
        if bool(candidates) != bool(expected["candidate"]):
            return False
    if not candidates:
        return True
    candidate = max(candidates, key=lambda item: item["lengthMetres"])
    checks = {
        "confidence": candidate["confidence"],
        "startBoundary": candidate["startBoundary"]["type"],
        "endBoundary": candidate["endBoundary"]["type"],
    }
    for key, actual in checks.items():
        if key in expected and actual != expected[key]:
            return False
        if f"{key}Not" in expected and actual == expected[f"{key}Not"]:
            return False
    return True


@click.command("overlap-diagnostics")
@click.argument(
    "fixture_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--fail-on-mismatch",
    is_flag=True,
    help="Exit unsuccessfully when a case differs from its expected diagnostics.",
)
def overlap_diagnostics_command(
    fixture_file: Path,
    fail_on_mismatch: bool,
) -> None:
    fixture = json.loads(fixture_file.read_text())
    mismatches = 0
    for case in fixture["cases"]:
        analysis = analyze_overlap_case(case)
        matches = overlap_case_matches_expectation(case, analysis)
        mismatches += not matches
        summary = analysis["summary"]
        click.echo(
            f"{'PASS' if matches else 'FAIL'} {case['name']}: "
            f"{summary['candidateCount']} candidate(s), "
            f"{summary['candidateDistanceMetres']} m"
        )
        for candidate in analysis["candidates"]:
            click.echo(
                "  "
                f"{candidate['confidence']} · {candidate['lengthMetres']} m · "
                f"median {candidate['medianSeparationMetres']} m · "
                f"{candidate['startBoundary']['type']} → "
                f"{candidate['endBoundary']['type']} · "
                f"saved {candidate['savedPathSegmentIds']}"
            )
    click.echo(f"{len(fixture['cases']) - mismatches}/{len(fixture['cases'])} cases matched.")
    if fail_on_mismatch and mismatches:
        raise click.ClickException(f"{mismatches} overlap case(s) did not match.")


def init_app(app: Flask) -> None:
    app.cli.add_command(seed_network_command)
    app.cli.add_command(overlap_diagnostics_command)
