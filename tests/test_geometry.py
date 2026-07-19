import pytest

from path_network.geometry import (
    leg_intersection,
    project_point_to_geometry,
    projector_for,
    split_geometry,
)


def test_local_projection_uses_metres_and_round_trips():
    projector = projector_for([[[13.4, 52.5], [13.401, 52.501]]])

    origin = projector.project([13.4, 52.5])
    nearby = projector.project([13.401, 52.5])

    assert nearby[0] - origin[0] == pytest.approx(67.7, abs=0.5)
    assert projector.unproject(nearby) == pytest.approx([13.401, 52.5])


def test_leg_intersection_returns_fractions_for_x_crossing():
    crossing = leg_intersection((0, 0), (10, 10), (0, 10), (10, 0))

    assert crossing is not None
    assert crossing[0] == pytest.approx(0.5)
    assert crossing[1] == pytest.approx(0.5)
    assert crossing[2] == pytest.approx((5, 5))


def test_collinear_single_point_contact_is_an_intersection_but_overlap_is_not():
    contact = leg_intersection((0, 0), (10, 0), (10, 0), (20, 0))
    overlap = leg_intersection((0, 0), (10, 0), (5, 0), (15, 0))

    assert contact is not None
    assert contact[2] == pytest.approx((10, 0))
    assert overlap is None


def test_split_geometry_preserves_order_and_shared_junction():
    start = {"id": "start"}
    crossing = {"id": "crossing"}
    end = {"id": "end"}

    pieces = split_geometry(
        [[0, 0], [10, 0], [20, 0]],
        [
            {"position": 0, "coordinate": [0, 0], "node": start},
            {"position": 1.5, "coordinate": [15, 0], "node": crossing},
            {"position": 2, "coordinate": [20, 0], "node": end},
        ],
    )

    assert [piece["geometry"] for piece in pieces] == [
        [[0.0, 0.0], [10.0, 0.0], [15.0, 0.0]],
        [[15.0, 0.0], [20.0, 0.0]],
    ]
    assert pieces[0]["endNode"] is pieces[1]["startNode"]


def test_project_point_to_geometry_uses_closest_leg_and_interpolates_elevation():
    position, coordinate, separation = project_point_to_geometry(
        [13.0015, 52.0002],
        [[13.0, 52.0, 10], [13.001, 52.0, 20], [13.002, 52.0, 30]],
    )

    assert position == pytest.approx(1.5, abs=0.01)
    assert coordinate == pytest.approx([13.0015, 52.0, 25], abs=1e-8)
    assert separation == pytest.approx(22.2, abs=0.5)
