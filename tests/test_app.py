import io
import json

from path_network import create_app

def test_upload_multiple_files(client, sample_gpx_bytes):
    response = client.post(
        "/api/gpx",
        data={
            "files": [
                (io.BytesIO(sample_gpx_bytes), "first.gpx"),
                (io.BytesIO(sample_gpx_bytes), "second.gpx"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert len(response.get_json()["routes"]) == 2


def test_rejects_non_gpx_file(client):
    response = client.post(
        "/api/gpx",
        data={"files": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["errors"][0]["message"] == "Only .gpx files are accepted."


def test_accepts_uppercase_gpx_extension(client, sample_gpx_bytes):
    response = client.post(
        "/api/gpx",
        data={"files": (io.BytesIO(sample_gpx_bytes), "ROUTE.GPX")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["routes"][0]["filename"] == "ROUTE.GPX"


def test_home_page(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"London Bus Map" in response.data
    assert b"/static/vendor/leaflet/leaflet.css" in response.data
    assert b"/static/vendor/leaflet/leaflet.js" in response.data
    assert b"Shared route network" in response.data
    assert b"Find a route" in response.data
    assert b"Open editor" in response.data
    assert b"Drop a GPX file here" not in response.data


def test_editor_page(client):
    response = client.get("/editor")
    assert response.status_code == 200
    assert b"Bus Map Editor" in response.data
    assert b"Create route" in response.data
    assert b"Active route direction" in response.data
    assert b"unpkg.com" not in response.data


def test_editor_mutations_require_authentication(tmp_path):
    app = create_app(
        {
            "TESTING": False,
            "DATABASE": str(tmp_path / "secured.sqlite"),
            "DATA_DIR": str(tmp_path / "data"),
            "EDITOR_PASSWORD": "secret-test-password",
            "SECRET_KEY": "test-secret-key",
        },
        instance_path=tmp_path / "instance",
    )
    client = app.test_client()
    assert client.get("/api/public/network").status_code == 200
    assert client.post("/api/edit-sessions").status_code == 401
    assert client.post("/api/editor/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/editor/login", json={"password": "secret-test-password"}).status_code == 200
    assert client.post("/api/edit-sessions").status_code == 201


def test_frontend_uses_edit_session_import_undo_and_cancel_endpoints(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b'fetch("/api/edit-sessions", { method: "POST" })' in response.data
    assert b"`/api/edit-sessions/${editSession.token}/imports`" in response.data
    assert b"`/api/edit-sessions/${editSession.token}/undo`" in response.data
    assert b"`/api/edit-sessions/${token}`" in response.data
    assert (
        b"`/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(segment.id)}`"
        in response.data
    )
    assert (
        b"`/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(segment.id)}/geometry`"
        in response.data
    )
    assert (
        b"`/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(selected.id)}/metadata`"
        in response.data
    )
    assert (
        b"`/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(selected.id)}/metadata`"
        in response.data
    )
    assert (
        b"`/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(junction.id)}`"
        in response.data
    )
    assert (
        b"`/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(junction.id)}/move`"
        in response.data
    )


def test_frontend_renders_edit_states_and_saves_complete_session(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b'state === "added"' in response.data
    assert b'["deleted", "replaced"].includes(state)' in response.data
    assert b"dashArray" in response.data
    assert b"saveButton.disabled = !editSession?.canCommit || isSaving" in response.data
    assert b"`/api/edit-sessions/${editSession.token}/commit`" in response.data
    assert b'showMessage("Changes saved.", "success")' in response.data
    assert b"window.confirm" in response.data
    assert b'method: "DELETE"' in response.data
    assert b"overlapDebug" in response.data
    assert b"renderOverlapDiagnostics" in response.data
    assert b"Reuse saved path" in response.data
    assert b"Keep uploaded path" in response.data
    assert b"/imports/overlaps/" in response.data
    assert b"incidentPathSegments" in response.data
    assert b"renderJunctionLegTable" in response.data
    assert b"leg-delete-button" in response.data
    assert b"segmentTraceTicks" in response.data
    assert b"renderSelectedTracePoints" in response.data
    assert b"trace-point-tick" in response.data
    assert b"stageTracePointMove" in response.data
    assert b"stageTracePointDelete" in response.data
    assert b"stageTracePointInsert" in response.data


def test_frontend_registers_keyboard_shortcuts(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b'document.addEventListener("keydown", handleKeyboardShortcut)' in response.data
    assert b'key === "escape"' in response.data
    assert b'key === "backspace" || key === "delete"' in response.data
    assert b'key === "a"' in response.data
    assert b'key === "enter" && newSegmentDraft' in response.data
    assert b'key === "i"' in response.data
    assert b'key === "m"' in response.data
    assert b'key === "n"' in response.data


def test_selection_inspector_uses_contextual_groups_and_shortcut_labels(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"const pathActionGroup" in response.data
    assert b"const junctionActionGroup" in response.data
    assert b"const newSegmentButton" in response.data
    assert b"const completeNewSegmentButton" in response.data
    assert b"function setButtonLabel(button, label, shortcut = \"\")" in response.data
    assert b"pathActionGroup.hidden = !isEditMode || !isPath || isInactive" in response.data
    assert b"tracePointSelected" in response.data
    assert b"junctionActionGroup.hidden = !isEditMode || isPath || isInactive" in response.data
    assert b"setButtonLabel(splitModeButton" in response.data
    assert b"setButtonLabel(traceInsertButton" in response.data
    assert b"setButtonLabel(newSegmentButton" in response.data
    assert b"setButtonLabel(completeNewSegmentButton" in response.data
    assert b"setButtonLabel(moveJunctionButton" in response.data
    assert b"function activeToolInfo(isPath, tracePointSelected)" in response.data
    assert b"function setActiveToolButton(button, active)" in response.data
    assert b"function renderActiveToolPanel(tool)" in response.data
    assert b"Active tool: Add junction" in response.data
    assert b"Active tool: Create path" in response.data
    assert b"Active tool: Move junction" in response.data
    assert b"Press Esc or click the active tool again to cancel" in response.data
    assert b'button.classList.toggle("is-active-tool", active)' in response.data
    assert b"Delete junction and leg" in response.data
    assert b"Delete and merge" in response.data
    assert b"mergeJunctionsButton.hidden = true" in response.data


def test_mode_shell_controls_visible_workflows(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b'let activeMode = "route"' in response.data
    assert b"const modeButtons" in response.data
    assert b"function setActiveMode(mode)" in response.data
    assert b'const showEditHome = activeMode === "edit" && !selected && !hasChanges' in response.data
    assert b"editHomeSection.hidden = !showEditHome" in response.data
    assert b'cleanupAreaButton.hidden = activeMode !== "edit"' in response.data
    assert b'routeDraftSection.hidden = activeMode !== "route"' in response.data
    assert b'pathActionGroup.hidden = !isEditMode || !isPath || isInactive' in response.data
    assert b"function startNewSegmentDraft()" in response.data
    assert b"function finishNewSegmentDraft(endJunction)" in response.data
    assert b"function completeNewSegmentAtLastPoint()" in response.data
    assert b"endCoordinate," in response.data
    assert b'`/api/edit-sessions/${editSession.token}/path-segments`' in response.data
    assert b"metadataPanel.hidden = !isEditMode || isInactive || tracePointSelected" in response.data
    assert b'sessionSection.hidden = activeMode === "route" || !hasChanges' in response.data
    assert b'if (activeMode !== "route")' in response.data
    assert b"renderNetwork(editSession?.network || savedNetwork)" in response.data
    assert b"positivePreferenceHighlight" in response.data
    assert b'color: "#4fbf70"' in response.data
    assert b'className: "positive-preference-highlight"' in response.data
    assert b"function drawPositivePreferenceHighlight" in response.data
    assert b"function offsetPathLatLngs" in response.data
    assert b'pathPreference(segment) === "destination"' in response.data
    assert b"[-offsetPixels, offsetPixels]" in response.data
    assert b"offsetPixels: 8" not in response.data
    assert b"if (!hasPositivePreference(segment))" not in response.data
    assert b'color: "#000000"' not in response.data
    assert b"operationList.replaceChildren();" in response.data
    assert b'modeButtons.forEach((button)' in response.data


def test_frontend_supports_route_drafting_and_gpx_export(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b'let routeDraftSteps = []' in response.data
    assert b"const routeDraftLayer" in response.data
    assert b'if (activeMode !== "route") return;' in response.data
    assert b"function buildRouteRequest()" in response.data
    assert b'version: "route-request-v1"' in response.data
    assert b"function toggleRouteSegment(segment)" in response.data
    assert b"either end of the first route segment" in response.data
    assert b"current route end" in response.data
    assert b"routeDraftSteps.splice(existingIndex)" in response.data
    assert b"function undoRouteDraftStep()" in response.data
    assert b"routeDraftSteps.pop()" in response.data
    assert b'undoRouteSegmentButton.addEventListener("click", undoRouteDraftStep)' in response.data
    assert b"function exportRouteDraftGpx()" in response.data
    assert b"application/gpx+xml" in response.data
    assert b"route-atlas-draft.gpx" in response.data
    assert b'if (activeMode === "route")' in response.data


def test_frontend_renders_metadata_editor(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"let metadataVocabulary" in response.data
    assert b"function renderMetadataForm(selected, type)" in response.data
    assert b"function readPathMetadataForm()" in response.data
    assert b"function readJunctionMetadataForm()" in response.data
    assert b"function scheduleMetadataStage" in response.data
    assert b"function flushMetadataStage" in response.data
    assert b"const endpoint = isPath" in response.data
    assert b"junctionProtected.checked" in response.data
    assert b"junctionPlaceType.value" in response.data
    assert b"function addJunctionPlaceBadge" in response.data
    assert b"junction-place-badge" in response.data
    assert b"lightrail" in response.data
    assert b"bus.fill" in response.data
    assert b'metadataPreference.addEventListener("change"' in response.data
    assert b'metadataNotes.addEventListener("input"' in response.data
    assert b'junctionProtected.addEventListener("change"' in response.data
    assert b'junctionPlaceType.addEventListener("change"' in response.data
    assert b'junctionName.addEventListener("input"' in response.data
    assert b'junctionNotes.addEventListener("input"' in response.data
    assert b"function loadMetadataVocabulary()" in response.data
    assert b'fetch("/static/metadata-vocabulary.json")' in response.data
    assert b'segment.origin === "metadata_edit"' in response.data
    assert b"metadataSaveButton" not in response.data


def test_metadata_vocabulary_static_file(client):
    response = client.get("/static/metadata-vocabulary.json")

    assert response.status_code == 200
    vocabulary = json.loads(response.data)
    assert [item["id"] for item in vocabulary["preference"]["values"]] == [
        "hard_avoid",
        "avoid",
        "like",
        "destination",
    ]
    route_flags = {item["id"] for item in vocabulary["route_flags"]["values"]}
    assert {"stile", "muddy", "sunny", "shade", "scenic"}.issubset(route_flags)
    place_types = {item["id"] for item in vocabulary["junction_place_type"]["values"]}
    assert {
        "train_station",
        "bus_stop",
        "manor_palace",
        "viewpoint",
        "church",
    }.issubset(place_types)
    icons = {item["id"]: item["icon"] for item in vocabulary["junction_place_type"]["values"]}
    assert icons["train_station"] == "lightrail"
    assert icons["bus_stop"] == "bus.fill"


def test_junction_cleanup_uses_local_bounds(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"const junctionCleanupRadiusMetres = 80" in response.data
    assert b"const cleanJunctionButton" in response.data
    assert b"function boundsAroundCoordinate(latitude, longitude, radiusMetres)" in response.data
    assert b"function stageSelectedJunctionCleanup()" in response.data
    assert b'stageAreaCleanup(bounds, "around the selected junction")' in response.data
    assert b'cleanJunctionButton.addEventListener("click", stageSelectedJunctionCleanup)' in response.data


def test_ordinary_staged_deletes_do_not_confirm(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"Delete this path segment when the staged changes are saved?" not in response.data
    assert b"Remove this junction and merge its two path segments?" not in response.data
    assert b"Discard all staged changes?" in response.data
    assert b"Clean up duplicate path?" in response.data


def test_selection_styles_keep_junction_ring_in_css(client):
    response = client.get("/static/styles.css")

    assert response.status_code == 200
    assert b".junction-selected" in response.data
    assert b"stroke-width: 4px" in response.data
    assert b".positive-preference-highlight" in response.data
    assert b"stroke: #4fbf70 !important" in response.data


def test_selected_path_opacity_is_applied_to_leaflet_layer(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"const selectedPathOpacityFactor = 0.55" in response.data
    assert b"function selectedOpacityForLayer(layer)" in response.data
    assert b"visibleLine._networkBaseStyle = { opacity: visibleLine.options.opacity }" in response.data
    assert b"? selectedOpacityForLayer(layer)" in response.data


def test_junction_move_placement_accepts_clicks_on_geometry_layers(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert b"if (newSegmentDraft) {" in response.data
    assert b"finishNewSegmentDraft(junction);" in response.data
    assert (
        b"""if (isJunctionMovePlacement) {
      if (
        selectedObject?.type === "junction"
        && String(selectedObject.id) !== String(junction.id)
      ) {
        stageJunctionPairMerge(junction.id, selectedObject.id);
        return;
      }
      stageJunctionMove(event.latlng);"""
        in response.data
    )
    assert (
        b"""hitLine.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      if (isJunctionMovePlacement) {
        stageJunctionMove(event.latlng, segment.id);"""
        in response.data
    )
    assert b"targetPathSegmentId," in response.data


def test_uses_isolated_test_paths(app, tmp_path):
    assert app.testing
    assert app.instance_path == str(tmp_path / "instance")
    assert app.config["DATA_DIR"] == str(tmp_path / "data")
    assert app.config["DATABASE"] == str(tmp_path / "path-network.sqlite")
    assert (tmp_path / "instance").is_dir()
    assert (tmp_path / "data").is_dir()
