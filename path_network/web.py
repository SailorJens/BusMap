from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, session

from path_network.bus_repository import (
    get_bus_snapshot,
    list_routes,
)

from path_network.edit_session import (
    EditSessionConflict,
    EditSessionError,
    EditSessionValidation,
    cancel_edit_session,
    commit_edit_session,
    compare_duplicate_cleanup,
    create_edit_session,
    create_path_segment,
    edit_junction_metadata,
    delete_path_segment,
    edit_path_geometry,
    edit_path_metadata,
    get_edit_session,
    import_trace,
    merge_at_junction,
    merge_junctions,
    move_junction,
    reset_overlap_boundary_adjustment,
    set_overlap_decision,
    set_overlap_boundary_adjustment,
    stage_area_duplicate_cleanup,
    stage_duplicate_cleanup,
    split_path_segment,
    stage_create_bus_route,
    stage_create_route_direction,
    stage_update_route_direction,
    stage_route_membership,
    stage_remove_route_membership,
    stage_segment_direction,
    undo_edit_session,
)
from path_network.gpx import GpxError, parse_gpx
from path_network.import_draft import (
    ImportDraftError,
)
from path_network.repository import get_path_network


web = Blueprint("web", __name__)


@web.get("/")
def index():
    return render_template(
        "viewer.html",
        tile_url=current_app.config["OSM_TILE_URL"],
        tile_attribution=current_app.config["OSM_ATTRIBUTION"],
        tile_max_zoom=current_app.config["OSM_MAX_ZOOM"],
    )


@web.get("/editor")
def editor():
    return render_template(
        "editor.html",
        tile_url=current_app.config["OSM_TILE_URL"],
        tile_attribution=current_app.config["OSM_ATTRIBUTION"],
        tile_max_zoom=current_app.config["OSM_MAX_ZOOM"],
        authenticated=bool(session.get("editor_authenticated")),
    )


@web.post("/api/editor/login")
def editor_login():
    payload = request.get_json(silent=True) or {}
    if payload.get("password") != current_app.config["EDITOR_PASSWORD"]:
        return jsonify({"error": "Incorrect editor password."}), 401
    session.clear()
    session["editor_authenticated"] = True
    return jsonify({"authenticated": True})


@web.post("/api/editor/logout")
def editor_logout():
    session.clear()
    return jsonify({"authenticated": False})


@web.before_request
def protect_editor_mutations():
    if current_app.config.get("TESTING"):
        return None
    public_posts = {"/api/editor/login", "/api/editor/logout"}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path not in public_posts:
        if not session.get("editor_authenticated"):
            return jsonify({"error": "Editor authentication required."}), 401
    return None


def _public_network_payload():
    network = get_path_network()
    bus = get_bus_snapshot()
    memberships = bus["routeMemberships"]
    directions = {
        direction["id"]: {**direction, "route": route}
        for route in bus["routes"]
        for direction in route["directions"]
    }
    by_segment: dict[int, list[dict]] = {}
    for membership in memberships:
        direction = directions.get(membership["routeDirectionId"])
        if direction:
            by_segment.setdefault(membership["pathSegmentId"], []).append({
                "routeId": direction["route"]["id"],
                "routeCode": direction["route"]["routeCode"],
                "colour": direction["route"]["colour"],
                "routeDirectionId": direction["id"],
                "directionName": direction["displayName"],
                "traversal": membership["traversal"],
            })
    for segment in network["pathSegments"]:
        segment["routeMemberships"] = by_segment.get(segment["id"], [])
    return {**network, **bus}


@web.get("/api/public/network")
def public_network():
    return jsonify(_public_network_payload())


@web.get("/api/public/routes")
def public_routes():
    return jsonify({"routes": list_routes()})


@web.get("/api/public/segments/<int:segment_id>")
def public_segment(segment_id: int):
    segment = next(
        (item for item in _public_network_payload()["pathSegments"] if item["id"] == segment_id),
        None,
    )
    return (jsonify(segment), 200) if segment else (jsonify({"error": "Segment not found."}), 404)


@web.post("/api/edit-sessions/<token>/routes")
def stage_route(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(stage_create_bus_route(
            current_app, token, payload.get("routeCode"), payload.get("displayName"), payload.get("colour")
        )), 201
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/routes/<route_id>/directions")
def stage_direction(token: str, route_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(stage_create_route_direction(
            current_app, token, route_id, payload.get("startJunctionId"),
            payload.get("endJunctionId"), payload.get("customDirectionName")
        )), 201
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.patch("/api/edit-sessions/<token>/route-directions/<direction_id>")
def stage_direction_update(token: str, direction_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(stage_update_route_direction(
            current_app, token, direction_id, payload.get("startJunctionId"),
            payload.get("endJunctionId"), payload.get("customDirectionName")
        ))
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/route-directions/<direction_id>/segments/<segment_id>")
def stage_membership(token: str, direction_id: str, segment_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(stage_route_membership(
            current_app, token, direction_id, segment_id, payload.get("traversal", "both")
        ))
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>/route-directions/<direction_id>/segments/<segment_id>")
def stage_membership_removal(token: str, direction_id: str, segment_id: str):
    try:
        return jsonify(stage_remove_route_membership(current_app, token, direction_id, segment_id))
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.patch("/api/edit-sessions/<token>/path-segments/<segment_id>/direction")
def stage_direction_mode(token: str, segment_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(stage_segment_direction(current_app, token, segment_id, payload.get("directionMode")))
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/gpx")
def upload_gpx():
    files = request.files.getlist("files")
    if not files or all(not file.filename for file in files):
        return jsonify({"error": "Choose at least one GPX file."}), 400

    routes = []
    errors = []
    for file in files:
        if not file.filename:
            continue
        if not file.filename.lower().endswith(".gpx"):
            errors.append({"filename": file.filename, "message": "Only .gpx files are accepted."})
            continue
        try:
            routes.append(
                parse_gpx(
                    file.stream,
                    file.filename,
                    max_file_size=current_app.config["GPX_MAX_FILE_SIZE"],
                )
            )
        except GpxError as error:
            errors.append({"filename": file.filename, "message": str(error)})

    status = 200 if routes else 400
    return jsonify({"routes": routes, "errors": errors}), status


@web.get("/api/path-network")
def path_network():
    return jsonify(get_path_network())


@web.post("/api/edit-sessions")
def create_session():
    return jsonify(create_edit_session(current_app)), 201


@web.get("/api/edit-sessions/<token>")
def load_session(token: str):
    try:
        return jsonify(get_edit_session(current_app, token))
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/imports")
def add_import(token: str):
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "Choose one GPX file."}), 400
    if not file.filename.lower().endswith(".gpx"):
        return jsonify({"error": "Only .gpx files are accepted."}), 400

    try:
        parsed_gpx = parse_gpx(
            file.stream,
            file.filename,
            max_file_size=current_app.config["GPX_MAX_FILE_SIZE"],
        )
        return jsonify(
            import_trace(
                current_app,
                token,
                parsed_gpx,
            )
        ), 201
    except GpxError as error:
        return jsonify({"error": str(error)}), 400
    except ImportDraftError as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/undo")
def undo_session(token: str):
    try:
        return jsonify(undo_edit_session(current_app, token))
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/imports/overlaps/<candidate_key>")
def decide_overlap(token: str, candidate_key: str):
    payload = request.get_json(silent=True) or {}
    if payload.get("decision") not in {"reuse", "keep"}:
        return jsonify({"error": "Overlap decision must be either reuse or keep."}), 400
    try:
        return jsonify(
            set_overlap_decision(
                current_app,
                token,
                candidate_key,
                payload.get("decision"),
            )
        )
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>/imports/overlaps/<candidate_key>")
def reset_overlap(token: str, candidate_key: str):
    try:
        return jsonify(
            set_overlap_decision(
                current_app,
                token,
                candidate_key,
                None,
            )
        )
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/imports/overlaps/<candidate_key>/boundaries/<boundary>")
def adjust_overlap_boundary(token: str, candidate_key: str, boundary: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            set_overlap_boundary_adjustment(
                current_app,
                token,
                candidate_key,
                boundary,
                payload.get("longitude"),
                payload.get("latitude"),
            )
        )
    except ImportDraftError as error:
        return jsonify({"error": str(error)}), 409
    except (TypeError, ValueError):
        return jsonify({"error": "Overlap boundary placement needs longitude and latitude."}), 400
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>/imports/overlaps/<candidate_key>/boundaries")
def reset_overlap_boundaries(token: str, candidate_key: str):
    try:
        return jsonify(
            reset_overlap_boundary_adjustment(
                current_app,
                token,
                candidate_key,
            )
        )
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except ImportDraftError as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/path-segments/<path_segment_id>/split")
def split_segment(token: str, path_segment_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            split_path_segment(
                current_app,
                token,
                path_segment_id,
                payload.get("longitude"),
                payload.get("latitude"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>/path-segments/<path_segment_id>")
def delete_segment(token: str, path_segment_id: str):
    try:
        return jsonify(
            delete_path_segment(
                current_app,
                token,
                path_segment_id,
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/path-segments")
def create_segment(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            create_path_segment(
                current_app,
                token,
                payload.get("startJunctionId"),
                payload.get("endJunctionId"),
                payload.get("geometry"),
                end_coordinate=payload.get("endCoordinate"),
                target_path_segment_id=payload.get("targetPathSegmentId"),
                start_coordinate=payload.get("startCoordinate"),
                route_direction_id=payload.get("routeDirectionId"),
                traversal=payload.get("traversal", "both"),
            )
        ), 201
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/route-drawings")
def create_route_drawing(token: str):
    payload = request.get_json(silent=True) or {}
    start = payload.get("startTarget") or {}
    end = payload.get("endTarget") or {}
    try:
        if start.get("type") == "existing_segment":
            if end.get("type") == "existing_segment":
                raise EditSessionValidation("Drawing between two segment interiors is not yet supported in one action.")
            traversal = payload.get("traversal", "both")
            traversal = {"start_to_end": "end_to_start", "end_to_start": "start_to_end"}.get(traversal, traversal)
            result = create_path_segment(
                current_app,
                token,
                end.get("junctionId") if end.get("type") == "existing_node" else None,
                None,
                list(reversed(payload.get("coordinates") or [])),
                start_coordinate=[end.get("longitude"), end.get("latitude")]
                if end.get("type") == "new_point" else None,
                end_coordinate=[start.get("longitude"), start.get("latitude")],
                target_path_segment_id=start.get("pathSegmentId"),
                route_direction_id=payload.get("routeDirectionId"),
                traversal=traversal,
            )
            result["createdPathSegment"]["continuationJunctionId"] = result["createdPathSegment"]["startJunctionId"]
            return jsonify(result), 201
        result = create_path_segment(
            current_app,
            token,
            start.get("junctionId") if start.get("type") == "existing_node" else None,
            end.get("junctionId") if end.get("type") == "existing_node" else None,
            payload.get("coordinates"),
            start_coordinate=[start.get("longitude"), start.get("latitude")]
            if start.get("type") == "new_point" else None,
            end_coordinate=[end.get("longitude"), end.get("latitude")]
            if end.get("type") in {"new_point", "existing_segment"} else None,
            target_path_segment_id=end.get("pathSegmentId")
            if end.get("type") == "existing_segment" else None,
            route_direction_id=payload.get("routeDirectionId"),
            traversal=payload.get("traversal", "both"),
        )
        result["createdPathSegment"]["continuationJunctionId"] = result["createdPathSegment"]["endJunctionId"]
        return jsonify(result), 201
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/path-segments/<path_segment_id>/geometry")
def edit_segment_geometry(token: str, path_segment_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            edit_path_geometry(
                current_app,
                token,
                path_segment_id,
                payload.get("geometry"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/path-segments/<path_segment_id>/metadata")
def edit_segment_metadata(token: str, path_segment_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            edit_path_metadata(
                current_app,
                token,
                path_segment_id,
                payload.get("metadata"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.put("/api/edit-sessions/<token>/junctions/<junction_id>/metadata")
def edit_selected_junction_metadata(token: str, junction_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            edit_junction_metadata(
                current_app,
                token,
                junction_id,
                payload.get("metadata"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/duplicate-cleanups/compare")
def compare_duplicate_paths(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            compare_duplicate_cleanup(
                current_app,
                token,
                payload.get("firstPathSegmentId"),
                payload.get("secondPathSegmentId"),
                retained_path_segment_id=payload.get("retainedPathSegmentId"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/duplicate-cleanups")
def cleanup_duplicate_paths(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            stage_duplicate_cleanup(
                current_app,
                token,
                payload.get("retainedPathSegmentId"),
                payload.get("removedPathSegmentId"),
            )
        )
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/duplicate-cleanups/area")
def cleanup_duplicate_paths_in_area(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            stage_area_duplicate_cleanup(
                current_app,
                token,
                payload.get("bounds") or {},
            )
        )
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>/junctions/<junction_id>")
def merge_junction(token: str, junction_id: str):
    try:
        return jsonify(
            merge_at_junction(
                current_app,
                token,
                junction_id,
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/junctions/<junction_id>/merge")
def merge_selected_junctions(token: str, junction_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            merge_junctions(
                current_app,
                token,
                junction_id,
                payload.get("targetJunctionId"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/junctions/<junction_id>/move")
def move_selected_junction(token: str, junction_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            move_junction(
                current_app,
                token,
                junction_id,
                payload.get("longitude"),
                payload.get("latitude"),
                payload.get("targetPathSegmentId"),
            )
        )
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.post("/api/edit-sessions/<token>/commit")
def commit_session(token: str):
    try:
        return jsonify(commit_edit_session(current_app, token))
    except EditSessionConflict as error:
        return jsonify({"error": str(error)}), 409
    except EditSessionValidation as error:
        return jsonify({"error": str(error)}), 400
    except EditSessionError as error:
        return jsonify({"error": str(error)}), 404


@web.delete("/api/edit-sessions/<token>")
def cancel_session(token: str):
    if not cancel_edit_session(current_app, token):
        return jsonify({"error": "Edit session not found."}), 404
    return "", 204


@web.app_errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "The upload is larger than 50 MB."}), 413
