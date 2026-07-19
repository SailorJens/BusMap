import pytest

from path_network.bus_repository import (
    create_direction,
    create_route,
    direction_display_name,
    get_bus_snapshot,
    remove_membership,
    set_membership,
)
from path_network.repository import (
    RepositoryError,
    create_junction,
    create_path_segment,
    get_path_network_revision,
)


def test_routes_directions_and_memberships_persist(app):
    with app.app_context():
        west = create_junction(-0.2, 51.5)
        east = create_junction(-0.1, 51.5)
        segment = create_path_segment(west["id"], east["id"], [[-0.2, 51.5], [-0.1, 51.5]])
        route = create_route("73", "Stoke Newington – Victoria", "#e32017")
        direction = create_direction(route["id"], west["id"], east["id"])
        membership = set_membership(direction["id"], segment["id"], "start_to_end")
        snapshot = get_bus_snapshot()

    assert snapshot["routes"][0]["routeCode"] == "73"
    assert snapshot["routes"][0]["directions"][0]["displayName"] == "Eastbound"
    assert membership["traversal"] == "start_to_end"
    assert snapshot["routeMemberships"][0]["pathSegmentId"] == segment["id"]


def test_route_code_is_case_insensitively_unique(app):
    with app.app_context():
        create_route("N73")
        with pytest.raises(RepositoryError, match="already exists"):
            create_route("n73")


def test_direction_names_cover_cardinal_bearings():
    base = {"startLongitude": 0, "startLatitude": 0, "customDirectionName": None}
    assert direction_display_name({**base, "endLongitude": 0, "endLatitude": 1}) == "Northbound"
    assert direction_display_name({**base, "endLongitude": 1, "endLatitude": 0}) == "Eastbound"
    assert direction_display_name({**base, "endLongitude": 0, "endLatitude": -1}) == "Southbound"
    assert direction_display_name({**base, "endLongitude": -1, "endLatitude": 0}) == "Westbound"


def test_membership_respects_one_way_segment(app):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(a["id"], b["id"], [[-0.2, 51.5], [-0.1, 51.5]], direction_mode="start_to_end")
        direction = create_direction(create_route("12")["id"], a["id"], b["id"])
        with pytest.raises(RepositoryError, match="contradicts"):
            set_membership(direction["id"], segment["id"], "end_to_start")


def test_route_changes_affect_network_revision(app):
    with app.app_context():
        before = get_path_network_revision()
        create_route("159")
        assert get_path_network_revision() != before


def test_public_network_lists_routes_on_each_segment(app, client):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(a["id"], b["id"], [[-0.2, 51.5], [-0.1, 51.5]])
        direction = create_direction(create_route("88", colour="#0019a8")["id"], a["id"], b["id"])
        set_membership(direction["id"], segment["id"], "start_to_end")

    response = client.get("/api/public/network")
    assert response.status_code == 200
    assert response.get_json()["pathSegments"][0]["routeMemberships"][0]["routeCode"] == "88"


def test_remove_membership(app):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(a["id"], b["id"], [[-0.2, 51.5], [-0.1, 51.5]])
        direction = create_direction(create_route("24")["id"])
        set_membership(direction["id"], segment["id"], "both")
        assert remove_membership(direction["id"], segment["id"])
        assert not get_bus_snapshot()["routeMemberships"]


def test_split_copies_membership_and_direction_mode_to_both_children(app, client):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(
            a["id"], b["id"], [[-0.2, 51.5], [-0.15, 51.51], [-0.1, 51.5]],
            direction_mode="start_to_end",
        )
        direction = create_direction(create_route("73")["id"], a["id"], b["id"])
        set_membership(direction["id"], segment["id"], "start_to_end")

    session = client.post("/api/edit-sessions").get_json()
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/split",
        json={"longitude": -0.15, "latitude": 51.51},
    )
    assert staged.status_code == 200
    committed = client.post(f"/api/edit-sessions/{session['token']}/commit")
    assert committed.status_code == 200
    with app.app_context():
        snapshot = get_bus_snapshot()
        children = committed.get_json()["network"]["pathSegments"]
        assert len(snapshot["routeMemberships"]) == 2
        assert {m["pathSegmentId"] for m in snapshot["routeMemberships"]} == {s["id"] for s in children}
        assert {s["direction_mode"] for s in children} == {"start_to_end"}


def test_route_workflow_is_staged_undoable_and_committed_atomically(app, client):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(a["id"], b["id"], [[-0.2, 51.5], [-0.1, 51.5]])
    session = client.post("/api/edit-sessions").get_json()
    created = client.post(
        f"/api/edit-sessions/{session['token']}/routes",
        json={"routeCode": "476", "colour": "#e32017"},
    ).get_json()
    route = created["routes"][-1]
    directed = client.post(
        f"/api/edit-sessions/{session['token']}/routes/{route['id']}/directions",
        json={"startJunctionId": a["id"], "endJunctionId": b["id"]},
    ).get_json()
    direction = directed["routeDirections"][-1]
    assigned = client.put(
        f"/api/edit-sessions/{session['token']}/route-directions/{direction['id']}/segments/{segment['id']}",
        json={"traversal": "start_to_end"},
    ).get_json()
    assert assigned["canUndo"]
    assert assigned["routeMemberships"][0]["pathSegmentId"] == segment["id"]
    undone = client.post(f"/api/edit-sessions/{session['token']}/undo").get_json()
    assert undone["routeMemberships"] == []
    client.put(
        f"/api/edit-sessions/{session['token']}/route-directions/{direction['id']}/segments/{segment['id']}",
        json={"traversal": "start_to_end"},
    )
    saved = client.post(f"/api/edit-sessions/{session['token']}/commit")
    assert saved.status_code == 200
    with app.app_context():
        snapshot = get_bus_snapshot()
        assert snapshot["routes"][0]["routeCode"] == "476"
        assert snapshot["routeMemberships"][0]["traversal"] == "start_to_end"


def test_route_drawing_can_start_and_finish_on_empty_map(app, client):
    session = client.post("/api/edit-sessions").get_json()
    staged_route = client.post(
        f"/api/edit-sessions/{session['token']}/routes", json={"routeCode": "N73"}
    ).get_json()
    route = staged_route["routes"][-1]
    staged_direction = client.post(
        f"/api/edit-sessions/{session['token']}/routes/{route['id']}/directions", json={}
    ).get_json()
    direction = staged_direction["routeDirections"][-1]
    response = client.post(
        f"/api/edit-sessions/{session['token']}/route-drawings",
        json={
            "routeDirectionId": direction["id"],
            "startTarget": {"type": "new_point", "longitude": -0.15, "latitude": 51.50},
            "coordinates": [[-0.15, 51.50], [-0.14, 51.51], [-0.13, 51.52]],
            "endTarget": {"type": "new_point", "longitude": -0.13, "latitude": 51.52},
            "traversal": "start_to_end",
        },
    )
    assert response.status_code == 201
    staged = response.get_json()
    assert len([n for n in staged["network"]["junctions"] if n["state"] == "added"]) == 2
    assert len([s for s in staged["network"]["pathSegments"] if s["state"] == "added"]) == 1
    assert staged["routeMemberships"][0]["traversal"] == "start_to_end"
    assert len(staged["operations"]) == 3
    saved = client.post(f"/api/edit-sessions/{session['token']}/commit")
    assert saved.status_code == 200
    with app.app_context():
        assert len(get_bus_snapshot()["routeMemberships"]) == 1


def test_route_drawing_can_start_by_splitting_an_existing_segment(app, client):
    with app.app_context():
        west = create_junction(-0.2, 51.5)
        east = create_junction(-0.1, 51.5)
        target = create_path_segment(west["id"], east["id"], [[-0.2, 51.5], [-0.1, 51.5]])
        route = create_route("12")
        direction = create_direction(route["id"])
    session = client.post("/api/edit-sessions").get_json()
    response = client.post(
        f"/api/edit-sessions/{session['token']}/route-drawings",
        json={
            "routeDirectionId": direction["id"],
            "startTarget": {"type": "existing_segment", "pathSegmentId": target["id"], "longitude": -0.15, "latitude": 51.5},
            "coordinates": [[-0.15, 51.5], [-0.15, 51.53]],
            "endTarget": {"type": "new_point", "longitude": -0.15, "latitude": 51.53},
            "traversal": "start_to_end",
        },
    )
    assert response.status_code == 201
    staged = response.get_json()
    assert staged["createdPathSegment"]["splitTargetPath"]
    assert len([s for s in staged["network"]["pathSegments"] if s["state"] == "added"]) == 3
    assert staged["routeMemberships"][0]["traversal"] == "end_to_start"


def test_segment_direction_is_staged_and_persisted(app, client):
    with app.app_context():
        a = create_junction(-0.2, 51.5)
        b = create_junction(-0.1, 51.5)
        segment = create_path_segment(a["id"], b["id"], [[-0.2, 51.5], [-0.1, 51.5]])
    session = client.post("/api/edit-sessions").get_json()
    staged = client.patch(
        f"/api/edit-sessions/{session['token']}/path-segments/{segment['id']}/direction",
        json={"directionMode": "start_to_end"},
    )
    assert staged.status_code == 200
    assert next(s for s in staged.get_json()["network"]["pathSegments"] if s["state"] == "added")["directionMode"] == "start_to_end"
    committed = client.post(f"/api/edit-sessions/{session['token']}/commit").get_json()
    assert committed["network"]["pathSegments"][0]["direction_mode"] == "start_to_end"


def test_standalone_drawing_requires_no_route(app, client):
    session = client.post("/api/edit-sessions").get_json()
    response = client.post(
        f"/api/edit-sessions/{session['token']}/route-drawings",
        json={
            "startTarget": {"type": "new_point", "longitude": -0.15, "latitude": 51.50},
            "coordinates": [[-0.15, 51.50], [-0.14, 51.51]],
            "endTarget": {"type": "new_point", "longitude": -0.14, "latitude": 51.51},
        },
    )
    assert response.status_code == 201
    staged = response.get_json()
    assert staged["routes"] == []
    assert staged["routeMemberships"] == []
    assert len([s for s in staged["network"]["pathSegments"] if s["state"] == "added"]) == 1


def test_newly_finished_segment_persists_a_degree_one_endpoint(app, client):
    session = client.post("/api/edit-sessions").get_json()
    staged = client.post(
        f"/api/edit-sessions/{session['token']}/route-drawings",
        json={
            "startTarget": {"type": "new_point", "longitude": -0.15, "latitude": 51.50},
            "coordinates": [[-0.15, 51.50], [-0.14, 51.51], [-0.13, 51.52]],
            "endTarget": {"type": "new_point", "longitude": -0.13, "latitude": 51.52},
        },
    ).get_json()
    endpoint_id = staged["createdPathSegment"]["endJunctionId"]
    committed = client.post(f"/api/edit-sessions/{session['token']}/commit").get_json()
    segments = committed["network"]["pathSegments"]
    permanent_endpoint = next(
        node for node in committed["network"]["junctions"]
        if node["longitude"] == -0.13 and node["latitude"] == 51.52
    )
    degree = sum(
        permanent_endpoint["id"] in {segment["start_junction_id"], segment["end_junction_id"]}
        for segment in segments
    )
    assert endpoint_id.startswith("created-junction-")
    assert degree == 1


def test_standalone_junction_can_be_added_selected_and_saved(app, client):
    session = client.post("/api/edit-sessions").get_json()
    response = client.post(
        f"/api/edit-sessions/{session['token']}/junctions",
        json={"longitude": -0.1278, "latitude": 51.5074},
    )
    assert response.status_code == 201
    staged = response.get_json()
    junction_id = staged["createdJunction"]["junctionId"]
    assert any(node["id"] == junction_id and node["state"] == "added" for node in staged["network"]["junctions"])
    committed = client.post(f"/api/edit-sessions/{session['token']}/commit")
    assert committed.status_code == 200
    nodes = committed.get_json()["network"]["junctions"]
    assert len(nodes) == 1
    assert nodes[0]["longitude"] == -0.1278
