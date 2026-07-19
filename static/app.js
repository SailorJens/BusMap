const colors = {
  saved: "#1f6b4f",
  added: "#e55c45",
  deleted: "#1f6b4f",
  replaced: "#1f6b4f",
  route: "#e13f32",
};
const selectedPathOpacityFactor = 0.55;
const selectedPathMinimumOpacity = 0.18;
const junctionCleanupRadiusMetres = 80;
let metadataVocabulary = {
  preference: {
    values: [
      { id: "hard_avoid", label: "Hard avoid" },
      { id: "avoid", label: "Avoid" },
      { id: "like", label: "Like" },
      { id: "destination", label: "Destination" },
    ],
  },
  route_flags: {
    values: [
      { id: "stile", label: "Stile" },
      { id: "muddy", label: "Muddy" },
      { id: "steps", label: "Steps" },
      { id: "rough", label: "Rough" },
      { id: "busy_road", label: "Busy road" },
      { id: "no_access", label: "No access" },
      { id: "overgrown", label: "Overgrown" },
      { id: "shade", label: "Shade" },
      { id: "sunny", label: "Sunny" },
      { id: "scenic", label: "Scenic" },
      { id: "view", label: "View" },
      { id: "water", label: "Water" },
      { id: "woods", label: "Woods" },
      { id: "quiet", label: "Quiet" },
    ],
  },
  junction_place_type: {
    values: [
      { id: "", label: "None", icon: "" },
      { id: "route_terminus", label: "Route Terminus", icon: "T" },
    ],
  },
};

let savedNetwork = { junctions: [], pathSegments: [], bounds: null };
let editSession = null;
let isSaving = false;
let selectedObject = null;
let isSplitPlacement = false;
let isJunctionMovePlacement = false;
let isAreaCleanupPlacement = false;
let isAddJunctionPlacement = false;
let selectedTracePointIndex = null;
let traceEditAction = null;
let areaCleanupStartLatLng = null;
let areaCleanupRectangle = null;
let ignoreNextMapClick = false;
let junctionMergeSourceId = null;
let overlapBoundaryPlacement = null;
let duplicateCleanupSourceId = null;
let newSegmentDraft = null;
let routeDraftSteps = [];
let routeReplacementStartJunctionId = null;
let activeMode = "edit";

const map = L.map("map", { zoomControl: false }).setView([51.1, 10.3], 6);
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const savedSegmentLayer = L.featureGroup().addTo(map);
const savedJunctionLayer = L.featureGroup().addTo(map);
const addedSegmentLayer = L.featureGroup().addTo(map);
const addedJunctionLayer = L.featureGroup().addTo(map);
const overlapDebugEnabled = new URLSearchParams(window.location.search).has("overlapDebug");
const overlapDebugLayer = L.featureGroup().addTo(map);
const overlapReviewLayer = L.featureGroup().addTo(map);
const tracePointLayer = L.featureGroup().addTo(map);
const newSegmentDraftLayer = L.featureGroup().addTo(map);
const routeDraftLayer = L.featureGroup().addTo(map);

const preferencePathStyles = {
  hard_avoid: { color: "#111111", weight: 4.2, dashArray: "1 8" },
  avoid: { color: "#111111", weight: 3.6, dashArray: null },
  ok: { color: "#2f6fb2", weight: 3.4, dashArray: null },
  like: { color: "#2f6fb2", weight: 3.4, dashArray: null },
  destination: { color: "#2f6fb2", weight: 3.4, dashArray: null },
};
const positivePreferenceHighlight = {
  weight: 3,
  opacity: 0.95,
  offsetPixels: 6,
};

const modeButtons = Array.from(document.querySelectorAll(".mode-button"));
const editHomeSection = document.querySelector("#edit-home-section");
const uploadForm = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const dropZone = document.querySelector("#drop-zone");
const message = document.querySelector("#message");
const fitButton = document.querySelector("#fit-button");
const cleanupAreaButton = document.querySelector("#cleanup-area-button");
const finishNewPathButton = document.querySelector("#finish-new-path-button");
const panelCleanupAreaButton = document.querySelector("#panel-cleanup-area-button");
const addJunctionButton = document.querySelector("#add-junction-button");
const mapStatus = document.querySelector("#map-status");
const mapOverlay = document.querySelector(".map-overlay");
const networkCounts = document.querySelector("#network-counts");
const networkSection = document.querySelector(".network-section");
const legendSection = document.querySelector(".legend-section");
const sessionSection = document.querySelector("#session-section");
const sessionEmptyState = document.querySelector("#session-empty-state");
const sessionSummary = document.querySelector("#session-summary");
const sessionRevision = document.querySelector("#session-revision");
const operationName = document.querySelector("#operation-name");
const changeCounts = document.querySelector("#change-counts");
const operationStats = document.querySelector("#operation-stats");
const duplicateSummary = document.querySelector("#duplicate-summary");
const overlapSummary = document.querySelector("#overlap-summary");
const overlapReviewList = document.querySelector("#overlap-review-list");
const operationList = document.querySelector("#operation-list");
const undoButton = document.querySelector("#undo-button");
const saveButton = document.querySelector("#save-button");
const cancelSessionButton = document.querySelector("#cancel-session-button");
const saveNote = document.querySelector("#save-note");
const selectionSection = document.querySelector("#selection-section");
const routeDraftSection = document.querySelector("#route-draft-section");
const undoRouteSegmentButton = document.querySelector("#undo-route-segment-button");
const clearRouteButton = document.querySelector("#clear-route-button");
const routeDraftLength = document.querySelector("#route-draft-length");
const routeDraftStatus = document.querySelector("#route-draft-status");
const routeDraftList = document.querySelector("#route-draft-list");
const exportRouteGpxButton = document.querySelector("#export-route-gpx-button");
const busRouteCode = document.querySelector("#bus-route-code");
const busRoutePicker = document.querySelector("#bus-route-picker");
const busRouteCodes = document.querySelector("#bus-route-codes");
const busRouteExistenceStatus = document.querySelector("#bus-route-existence-status");
const busRouteDirectionField = document.querySelector("#bus-route-direction-field");
const busRouteDirection = document.querySelector("#bus-route-direction");
const busRouteDirectionNameField = document.querySelector("#bus-route-direction-name-field");
const busRouteDirectionName = document.querySelector("#bus-route-direction-name");
const routeOpenEndField = document.querySelector("#route-open-end-field");
const routeOpenEndSide = document.querySelector("#route-open-end-side");
const stageBusRouteButton = document.querySelector("#stage-bus-route-button");
const saveRouteChangesButton = document.querySelector("#save-route-changes-button");
const selectionName = document.querySelector("#selection-name");
const selectionDetails = document.querySelector("#selection-details");
const pathActionGroup = document.querySelector("#path-action-group");
const junctionActionGroup = document.querySelector("#junction-action-group");
const splitModeButton = document.querySelector("#split-mode-button");
const deleteSegmentButton = document.querySelector("#delete-segment-button");
const duplicateCleanupButton = document.querySelector("#duplicate-cleanup-button");
const traceEditPanel = document.querySelector("#trace-edit-panel");
const traceMoveButton = document.querySelector("#trace-move-button");
const traceInsertButton = document.querySelector("#trace-insert-button");
const traceDeleteButton = document.querySelector("#trace-delete-button");
const metadataPanel = document.querySelector("#metadata-panel");
const pathMetadataFields = document.querySelector("#path-metadata-fields");
const junctionMetadataFields = document.querySelector("#junction-metadata-fields");
const metadataPreference = document.querySelector("#metadata-preference");
const metadataFlags = document.querySelector("#metadata-flags");
const metadataNotes = document.querySelector("#metadata-notes");
const junctionPlaceType = document.querySelector("#junction-place-type");
const junctionName = document.querySelector("#junction-name");
const junctionNotes = document.querySelector("#junction-notes");
const junctionProtected = document.querySelector("#junction-protected");
const cleanJunctionButton = document.querySelector("#clean-junction-button");
const newSegmentButton = document.querySelector("#new-segment-button");
const completeNewSegmentButton = document.querySelector("#complete-new-segment-button");
const moveJunctionButton = document.querySelector("#move-junction-button");
const mergeJunctionsButton = document.querySelector("#merge-junctions-button");
const mergeJunctionButton = document.querySelector("#merge-junction-button");
const junctionLegPanel = document.querySelector("#junction-leg-panel");
const junctionLegList = document.querySelector("#junction-leg-list");
const splitModeNote = document.querySelector("#split-mode-note");
const clearSelectionButton = document.querySelector("#clear-selection-button");
const selectionCard = document.querySelector(".selection-card");
const activeToolPanel = document.querySelector("#active-tool-panel");
const activeToolName = document.querySelector("#active-tool-name");
const activeToolInstruction = document.querySelector("#active-tool-instruction");
let metadataStageTimer = null;

function formatDistance(meters) {
  if (meters < 1000) return `${meters} m`;
  return `${(meters / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} km`;
}

function showMessage(text, type = "error") {
  message.textContent = text;
  message.className = `message ${type}`;
  message.hidden = false;
}

function hideMessage() {
  message.hidden = true;
}

function selectedOpacityForLayer(layer) {
  const baseOpacity = layer._networkBaseStyle?.opacity;
  if (typeof baseOpacity !== "number") return selectedPathMinimumOpacity;
  return Math.max(selectedPathMinimumOpacity, baseOpacity * selectedPathOpacityFactor);
}

function setButtonLabel(button, label, shortcut = "") {
  const text = document.createElement("span");
  text.textContent = label;
  if (!shortcut) {
    button.replaceChildren(text);
    return;
  }

  const key = document.createElement("kbd");
  key.className = "shortcut-key";
  key.textContent = shortcut;
  button.replaceChildren(text, key);
}

function pathPreference(segment) {
  const preference = segment?.metadata?.preference || "ok";
  return preferencePathStyles[preference] ? preference : "ok";
}

function hasPositivePreference(segment) {
  return ["like", "destination"].includes(pathPreference(segment));
}

function positivePreferenceHighlightOffsets(segment, offsetPixels) {
  return pathPreference(segment) === "destination"
    ? [-offsetPixels, offsetPixels]
    : [offsetPixels];
}

function pathPreferenceLabel(segment) {
  const preference = pathPreference(segment);
  if (preference === "ok") return "No preference";
  return (
    metadataVocabulary.preference.values.find((item) => item.id === preference)?.label
    || preference
  );
}

function visiblePathStyle(segment, state, isAdded, isRemoved) {
  if (isRemoved) {
    return {
      color: colors[state],
      weight: 4.5,
      opacity: 0.35,
      dashArray: "8 8",
    };
  }
  if (activeMode !== "route") {
    return {
      color: colors[state],
      weight: isAdded ? 5 : 4.5,
      opacity: isAdded ? 0.95 : 0.82,
      dashArray: null,
    };
  }
  return {
    color: colors.saved,
    weight: 4.5,
    opacity: 0.82,
    dashArray: null,
  };
}

function offsetPathLatLngs(latLngs, offsetPixels) {
  const points = latLngs.map((latlng) => map.latLngToLayerPoint(latlng));
  return points.map((point, index) => {
    const previous = points[Math.max(0, index - 1)];
    const next = points[Math.min(points.length - 1, index + 1)];
    const dx = next.x - previous.x;
    const dy = next.y - previous.y;
    const length = Math.hypot(dx, dy) || 1;
    const normalX = -dy / length;
    const normalY = dx / length;
    return map.layerPointToLatLng([
      point.x + normalX * offsetPixels,
      point.y + normalY * offsetPixels,
    ]);
  });
}

function drawPositivePreferenceHighlight(segment, latLngs, layer, options = {}) {
  if (activeMode !== "route" || !hasPositivePreference(segment)) return;
  const offsetPixels = options.offsetPixels ?? positivePreferenceHighlight.offsetPixels;
  positivePreferenceHighlightOffsets(segment, offsetPixels).forEach((offset) => {
    L.polyline(offsetPathLatLngs(latLngs, offset), {
      color: "#4fbf70",
      weight: options.weight ?? positivePreferenceHighlight.weight,
      opacity: options.opacity ?? positivePreferenceHighlight.opacity,
      lineCap: "round",
      lineJoin: "round",
      className: "positive-preference-highlight",
      interactive: false,
    }).addTo(layer);
  });
}

function pathSegmentTooltip(segment, state) {
  const base = `${state[0].toUpperCase()}${state.slice(1)} path · ${formatDistance(segment.distance_m)}`;
  return activeMode === "route" ? `${base} · ${pathPreferenceLabel(segment)}` : base;
}

function renderPathMetadataForm(segment) {
  const metadata = segment?.metadata || {};
  const preference = metadata.preference && metadata.preference !== "ok"
    ? metadata.preference
    : "";
  metadataPreference.replaceChildren(
    ...[
      { id: "", label: "Not set" },
      ...metadataVocabulary.preference.values,
    ].map((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      option.selected = item.id === preference;
      return option;
    })
  );

  const activeFlags = new Set(metadata.route_flags || []);
  metadataFlags.replaceChildren(
    ...metadataVocabulary.route_flags.values.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "metadata-flag";
      button.dataset.flag = item.id;
      button.textContent = item.label;
      button.classList.toggle("is-active", activeFlags.has(item.id));
      button.setAttribute("aria-pressed", activeFlags.has(item.id) ? "true" : "false");
      button.addEventListener("click", () => {
        const nextActive = !button.classList.contains("is-active");
        button.classList.toggle("is-active", nextActive);
        button.setAttribute("aria-pressed", nextActive ? "true" : "false");
        scheduleMetadataStage();
      });
      return button;
    })
  );
  metadataNotes.value = metadata.notes || "";
}

function renderJunctionMetadataForm(junction) {
  const metadata = junction?.metadata || {};
  const placeType = metadata.place_type || "";
  junctionPlaceType.replaceChildren(
    ...metadataVocabulary.junction_place_type.values.map((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      option.selected = item.id === placeType;
      return option;
    })
  );
  junctionName.value = metadata.name || "";
  junctionNotes.value = metadata.notes || "";
  junctionProtected.checked = ["end_of_route", "route_terminus"].includes(placeType) || Boolean(metadata.protected);
  junctionProtected.disabled = ["end_of_route", "route_terminus"].includes(placeType);
}

function renderMetadataForm(selected, type) {
  const isPath = type === "pathSegment";
  pathMetadataFields.hidden = !isPath;
  junctionMetadataFields.hidden = isPath;
  if (isPath) {
    renderPathMetadataForm(selected);
  } else {
    renderJunctionMetadataForm(selected);
  }
}

function readPathMetadataForm() {
  return {
    preference: metadataPreference.value || "",
    route_flags: Array.from(metadataFlags.querySelectorAll(".metadata-flag.is-active"))
      .map((button) => button.dataset.flag)
      .filter(Boolean),
    notes: metadataNotes.value,
  };
}

function readJunctionMetadataForm() {
  return {
    protected: junctionProtected.checked,
    place_type: junctionPlaceType.value || "",
    name: junctionName.value,
    notes: junctionNotes.value,
  };
}

function normalizePathMetadata(metadata = {}) {
  const preference = metadata.preference && metadata.preference !== "ok"
    ? metadata.preference
    : "";
  return {
    preference,
    route_flags: [...new Set(metadata.route_flags || [])].sort(),
    notes: metadata.notes || "",
  };
}

function normalizeJunctionMetadata(metadata = {}) {
  return {
    protected: Boolean(metadata.protected),
    place_type: metadata.place_type || "",
    name: metadata.name || "",
    notes: metadata.notes || "",
  };
}

function metadataChanged(selected, type) {
  const current = type === "pathSegment"
    ? normalizePathMetadata(selected?.metadata)
    : normalizeJunctionMetadata(selected?.metadata);
  const next = type === "pathSegment"
    ? normalizePathMetadata(readPathMetadataForm())
    : normalizeJunctionMetadata(readJunctionMetadataForm());
  return JSON.stringify(current) !== JSON.stringify(next);
}

function clearActiveToolState() {
  isSplitPlacement = false;
  isJunctionMovePlacement = false;
  isAreaCleanupPlacement = false;
  isAddJunctionPlacement = false;
  addJunctionButton.classList.remove("is-active-tool");
  addJunctionButton.textContent = "Add Junction";
  traceEditAction = null;
  junctionMergeSourceId = null;
  overlapBoundaryPlacement = null;
  duplicateCleanupSourceId = null;
  newSegmentDraft = null;
  clearAreaCleanupRectangle();
  map.dragging.enable();
  renderSelectedTracePoints();
  renderNewSegmentDraft();
}

function toggleAddJunctionPlacement() {
  if (!editSession || isSaving) return;
  const activate = !isAddJunctionPlacement;
  clearActiveToolState();
  isAddJunctionPlacement = activate;
  addJunctionButton.classList.toggle("is-active-tool", activate);
  addJunctionButton.textContent = activate ? "Click map to add junction…" : "Add Junction";
  if (activate) showMessage("Add Junction active. Click once on the map to place it.", "loading");
  else hideMessage();
}

async function placeStandaloneJunction(latlng) {
  if (!isAddJunctionPlacement || !editSession || isSaving) return;
  showMessage("Adding junction…", "loading");
  try {
    const response = await fetch(`/api/edit-sessions/${editSession.token}/junctions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ longitude: latlng.lng, latitude: latlng.lat }),
    });
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The junction could not be added.");
      return;
    }
    const junctionId = result.createdJunction?.junctionId;
    isAddJunctionPlacement = false;
    addJunctionButton.classList.remove("is-active-tool");
    addJunctionButton.textContent = "Add Junction";
    if (junctionId) selectedObject = { type: "junction", id: junctionId };
    renderSession(result);
    showMessage("Junction added and selected. Press N to draw a path from it.", "success");
  } catch {
    showMessage("Could not add the junction.");
  }
}

function activeNetwork() {
  return editSession?.network || savedNetwork;
}

function routeSegments() {
  const pathSegments = activeNetwork().pathSegments || [];
  return routeDraftSteps
    .map((step) => {
      const segment = pathSegments.find((candidate) =>
        String(candidate.id) === String(step.pathSegmentId) && candidate.state === "saved"
      );
      if (!segment) return null;
      return {
        ...segment,
        routeStartJunctionId: step.startJunctionId,
        routeEndJunctionId: step.endJunctionId,
      };
    })
    .filter(Boolean);
}

function routeSharedJunction(first, second) {
  const firstIds = [
    segmentJunctionId(first, "start"),
    segmentJunctionId(first, "end"),
  ].map(String);
  const secondIds = [
    segmentJunctionId(second, "start"),
    segmentJunctionId(second, "end"),
  ].map(String);
  return firstIds.find((id) => secondIds.includes(id)) || null;
}

function orientedRouteStep(segment, startJunctionId) {
  const segmentStartId = segmentJunctionId(segment, "start");
  const segmentEndId = segmentJunctionId(segment, "end");
  return {
    pathSegmentId: segment.id,
    startJunctionId,
    endJunctionId: String(startJunctionId) === String(segmentStartId)
      ? segmentEndId
      : segmentStartId,
  };
}

function normalizeSingleRouteStep() {
  if (routeDraftSteps.length !== 1) return;
  const segment = routeSegments()[0];
  if (!segment) return;
  routeDraftSteps[0] = {
    pathSegmentId: segment.id,
    startJunctionId: segmentJunctionId(segment, "start"),
    endJunctionId: segmentJunctionId(segment, "end"),
  };
}

function routeConnectivity(segments = routeSegments()) {
  const breaks = [];
  for (let index = 1; index < segments.length; index += 1) {
    if (String(segments[index - 1].routeEndJunctionId) !== String(segments[index].routeStartJunctionId)) {
      breaks.push(index);
    }
  }
  return { connected: breaks.length === 0, breaks };
}

function buildRouteRequest() {
  const segments = routeSegments();
  const connectivity = routeConnectivity(segments);
  return {
    version: "route-request-v1",
    source: "manual_draft",
    objective: "follow_ordered_path_segments",
    orderedPathSegmentIds: segments.map((segment) => segment.id),
    steps: segments.map((segment, index) => ({
      order: index + 1,
      pathSegmentId: segment.id,
      startJunctionId: segment.routeStartJunctionId,
      endJunctionId: segment.routeEndJunctionId,
      distanceMetres: segment.distance_m,
      metadata: segment.metadata || {},
    })),
    totalDistanceMetres: segments.reduce(
      (total, segment) => total + Number(segment.distance_m || 0),
      0
    ),
    connectivity: {
      connected: connectivity.connected,
      breakAfterOrders: connectivity.breaks,
    },
  };
}

function routeGeometryChunks() {
  const segments = routeSegments();
  const chunks = [];
  let current = [];
  let previousExitId = null;
  segments.forEach((segment, index) => {
    const startId = String(segmentJunctionId(segment, "start"));
    const endId = String(segmentJunctionId(segment, "end"));
    let geometry = segment.geometry.map((point) => [...point]);
    if (String(segment.routeStartJunctionId) === endId) {
      geometry = geometry.reverse();
    }
    const entryId = String(segment.routeStartJunctionId || startId);
    const exitId = String(segment.routeEndJunctionId || endId);
    if (index > 0 && previousExitId !== entryId) {
      if (current.length) chunks.push(current);
      current = geometry;
    } else if (current.length) {
      current = current.concat(geometry.slice(1));
    } else {
      current = geometry;
    }
    previousExitId = exitId;
  });
  if (current.length) chunks.push(current);
  return chunks;
}

function xmlEscape(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function routeDraftGpx() {
  const chunks = routeGeometryChunks();
  const segmentsXml = chunks.map((chunk) => {
    const points = chunk.map((point) => {
      const ele = point.length === 3 ? `<ele>${point[2]}</ele>` : "";
      return `<trkpt lat="${point[1]}" lon="${point[0]}">${ele}</trkpt>`;
    }).join("");
    return `<trkseg>${points}</trkseg>`;
  }).join("");
  return `<?xml version="1.0" encoding="UTF-8"?>\n`
    + `<gpx version="1.1" creator="Route Atlas" xmlns="http://www.topografix.com/GPX/1/1">`
    + `<trk><name>${xmlEscape("Route Atlas draft route")}</name>${segmentsXml}</trk></gpx>\n`;
}

function renderRouteDraftLayer() {
  routeDraftLayer.clearLayers();
  if (activeMode !== "route") return;
  const route = existingBusRouteForCode();
  if (route && busRouteDirection.value !== "__new__") {
    const routeDirectionIds = new Set(
      (editSession?.routeDirections || [])
        .filter((direction) => String(direction.busRouteId) === String(route.id) && direction.state !== "deleted")
        .filter((direction) => !busRouteDirection.value
          || String(direction.id) === String(busRouteDirection.value))
        .map((direction) => String(direction.id))
    );
    const segmentIds = new Set(
      (editSession?.routeMemberships || [])
        .filter((membership) => membership.state !== "deleted" && routeDirectionIds.has(String(membership.routeDirectionId)))
        .map((membership) => String(membership.pathSegmentId))
    );
    (activeNetwork().pathSegments || [])
      .filter((segment) => ["saved", "added"].includes(segment.state || "saved") && segmentIds.has(String(segment.id)))
      .forEach((segment) => {
        L.polyline(segment.geometry.map(([longitude, latitude]) => [latitude, longitude]), {
          color: colors.route,
          weight: 6,
          opacity: 1,
          lineCap: "round",
          lineJoin: "round",
          interactive: false,
        }).bindTooltip(`Route ${route.routeCode}`).addTo(routeDraftLayer);
      });
  }
  routeSegments().forEach((segment, index) => {
    const latLngs = segment.geometry.map(([longitude, latitude]) => [latitude, longitude]);
    L.polyline(latLngs, {
      color: colors.route,
      weight: 6,
      opacity: 1,
      lineCap: "round",
      lineJoin: "round",
      interactive: false,
    })
      .bindTooltip(`Route step ${index + 1} · ${formatDistance(segment.distance_m)}`)
      .addTo(routeDraftLayer);
  });
  routeDraftLayer.eachLayer((layer) => layer.bringToFront?.());
}

function fitExistingBusRoute() {
  const bounds = routeDraftLayer.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [35, 35] });
}

function renderRouteDraft() {
  const segments = routeSegments();
  const connectivity = routeConnectivity(segments);
  const totalDistance = segments.reduce(
    (total, segment) => total + Number(segment.distance_m || 0),
    0
  );
  routeDraftSection.hidden = activeMode !== "route";
  routeDraftLength.textContent = formatDistance(totalDistance);
  routeDraftStatus.textContent = segments.length === 0
    ? "No route segments selected."
    : (
      connectivity.connected
        ? `${segments.length} ${segments.length === 1 ? "segment" : "segments"} connected.`
        : `${segments.length} segments with ${connectivity.breaks.length} connection ${connectivity.breaks.length === 1 ? "break" : "breaks"}.`
  );
  undoRouteSegmentButton.disabled = segments.length === 0;
  clearRouteButton.disabled = segments.length === 0;
  exportRouteGpxButton.disabled = segments.length === 0;
  stageBusRouteButton.disabled = segments.length === 0 || !busRouteCode.value.trim();
  saveRouteChangesButton.disabled = !editSession?.canCommit || isSaving;
  renderBusRouteDirectionChoices();
  const existingSteps = orderedSelectedDirectionSteps();
  const routeJunctions = new Set(routeJunctionPositions(existingSteps).map(String));
  const draftEnd = routeDraftSteps.at(-1)?.endJunctionId;
  routeOpenEndField.hidden = !(
    segments.length && existingSteps.length
      && !routeJunctions.has(String(draftEnd))
  );
  if (segments.length && routeReplacementStartJunctionId != null) {
    stageBusRouteButton.textContent = routeOpenEndField.hidden
      ? "Replace route section"
      : "Save replacement at new terminus";
  }
  routeDraftList.replaceChildren(
    ...segments.map((segment, index) => {
      const item = document.createElement("li");
      item.textContent = `${index + 1}. Segment ${segment.id} · ${formatDistance(segment.distance_m)}`;
      return item;
    })
  );
  renderRouteDraftLayer();
}

function existingBusRouteForCode() {
  const code = busRouteCode.value.trim().toLowerCase();
  return (editSession?.routes || []).find(
    (route) => route.state !== "deleted" && route.routeCode.toLowerCase() === code
  ) || null;
}

function renderBusRouteDirectionChoices() {
  const availableRoutes = (editSession?.routes || []).filter((route) => route.state !== "deleted");
  const createOption = document.createElement("option");
  createOption.value = "";
  createOption.textContent = "Create a new route…";
  busRoutePicker.replaceChildren(
    createOption,
    ...availableRoutes.map((route) => {
      const option = document.createElement("option");
      option.value = route.id;
      option.textContent = `Route ${route.routeCode}${route.displayName ? ` · ${route.displayName}` : ""}`;
      return option;
    })
  );
  busRouteCodes.replaceChildren(
    ...availableRoutes.map((route) => {
      const option = document.createElement("option");
      option.value = route.routeCode;
      return option;
    })
  );
  const route = existingBusRouteForCode();
  const directions = route
    ? (editSession?.routeDirections || []).filter(
      (direction) => direction.state !== "deleted" && String(direction.busRouteId) === String(route.id)
    )
    : [];
  busRoutePicker.value = route ? String(route.id) : "";
  busRouteExistenceStatus.hidden = !busRouteCode.value.trim();
  if (busRouteCode.value.trim()) {
    const heading = document.createElement("span");
    const detail = document.createElement("small");
    if (route) {
      heading.textContent = `Route ${route.routeCode} already exists`;
      detail.textContent = directions.length
        ? `Editing ${directions.length} ${directions.length === 1 ? "direction" : "directions"}. Click a connected green segment to reroute, or click the first or last red segment to remove it.`
        : "You are editing this route. It does not have a direction yet.";
    } else {
      heading.textContent = `New Route ${busRouteCode.value.trim()}`;
      detail.textContent = "This route will be created when you add its first selected segments.";
    }
    busRouteExistenceStatus.replaceChildren(heading, detail);
  }
  busRouteDirectionField.hidden = !route;
  busRouteDirectionNameField.hidden = !route;
  if (!route) {
    busRouteDirection.replaceChildren();
    busRouteDirectionName.value = "";
    busRouteDirectionName.dataset.directionId = "";
    stageBusRouteButton.textContent = "Add route to edit session";
    renderRouteDraftLayer();
    return;
  }
  const previous = busRouteDirection.value;
  const createDirectionOption = document.createElement("option");
  createDirectionOption.value = "__new__";
  createDirectionOption.textContent = `+ Create Direction ${directions.length + 1}`;
  busRouteDirection.replaceChildren(
    ...directions.map((direction, index) => {
      const option = document.createElement("option");
      option.value = direction.id;
      const membershipCount = (editSession?.routeMemberships || []).filter(
        (membership) => membership.state !== "deleted"
          && String(membership.routeDirectionId) === String(direction.id)
      ).length;
      option.textContent = `${direction.displayName || `Direction ${index + 1}`} · ${membershipCount} ${membershipCount === 1 ? "segment" : "segments"}`;
      return option;
    }),
    ...(directions.length < 2 ? [createDirectionOption] : [])
  );
  if ([...busRouteDirection.options].some((option) => option.value === previous)) {
    busRouteDirection.value = previous;
  } else if (directions.length) {
    busRouteDirection.value = directions[0].id;
  } else if (directions.length < 2) {
    busRouteDirection.value = "__new__";
  }
  const selectedDirection = directions.find(
    (direction) => String(direction.id) === String(busRouteDirection.value)
  );
  const directionNameKey = selectedDirection ? String(selectedDirection.id) : "__new__";
  if (busRouteDirectionName.dataset.directionId !== directionNameKey) {
    busRouteDirectionName.value = selectedDirection
      ? (selectedDirection.customDirectionName || selectedDirection.displayName || "")
      : "";
    busRouteDirectionName.dataset.directionId = directionNameKey;
  }
  stageBusRouteButton.textContent = `Add segments to Route ${route.routeCode}`;
  renderRouteDraftLayer();
}

function selectedRouteDirection() {
  if (!editSession || busRouteDirection.value === "__new__") return null;
  return (editSession.routeDirections || []).find(
    (direction) => direction.state !== "deleted"
      && String(direction.id) === String(busRouteDirection.value)
  ) || null;
}

function orderedSelectedDirectionSteps() {
  const direction = selectedRouteDirection();
  if (!direction) return [];
  const memberships = (editSession.routeMemberships || []).filter(
    (membership) => membership.state !== "deleted"
      && String(membership.routeDirectionId) === String(direction.id)
  );
  const remaining = memberships.map((membership) => {
    const segment = (activeNetwork().pathSegments || []).find(
      (item) => String(item.id) === String(membership.pathSegmentId)
    );
    return segment ? { membership, segment } : null;
  }).filter(Boolean);
  if (!remaining.length) return [];
  const degree = new Map();
  remaining.forEach(({ segment }) => {
    [segmentJunctionId(segment, "start"), segmentJunctionId(segment, "end")].forEach((id) => {
      degree.set(String(id), (degree.get(String(id)) || 0) + 1);
    });
  });
  let current = direction.startJunctionId;
  if (current == null || !degree.has(String(current))) {
    current = [...degree.entries()].find(([, count]) => count === 1)?.[0]
      || segmentJunctionId(remaining[0].segment, "start");
  }
  const ordered = [];
  while (remaining.length) {
    const index = remaining.findIndex(({ segment }) => [
      segmentJunctionId(segment, "start"), segmentJunctionId(segment, "end"),
    ].some((id) => String(id) === String(current)));
    if (index < 0) break;
    const item = remaining.splice(index, 1)[0];
    const step = orientedRouteStep(item.segment, current);
    ordered.push({ ...step, segment: item.segment, membership: item.membership });
    current = step.endJunctionId;
  }
  return ordered;
}

function routeJunctionPositions(steps) {
  if (!steps.length) return [];
  return [steps[0].startJunctionId, ...steps.map((step) => step.endJunctionId)];
}

async function trimSelectedRouteEnd(segment) {
  const direction = selectedRouteDirection();
  const steps = orderedSelectedDirectionSteps();
  const index = steps.findIndex((step) => String(step.pathSegmentId) === String(segment.id));
  if (!direction || !steps.length || ![0, steps.length - 1].includes(index)) {
    showMessage("Only the first or last route segment can be removed directly.");
    return;
  }
  showMessage("Removing route-end segment…", "loading");
  try {
    let response = await fetch(
      `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}/segments/${encodeURIComponent(segment.id)}`,
      { method: "DELETE" }
    );
    let result = await response.json();
    if (!response.ok) throw new Error(result.error || "The route segment could not be removed.");
    const remaining = steps.filter((_, stepIndex) => stepIndex !== index);
    response = await fetch(
      `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: remaining[0]?.startJunctionId ?? null,
          endJunctionId: remaining.at(-1)?.endJunctionId ?? null,
          customDirectionName: direction.customDirectionName,
        }),
      }
    );
    result = await response.json();
    if (!response.ok) throw new Error(result.error || "The route endpoint could not be updated.");
    renderSession(result);
    showMessage("Route-end segment removed. Use Save changes to commit it.", "success");
  } catch (error) {
    showMessage(error.message || "The route segment could not be removed.");
  }
}

function toggleRouteSegment(segment) {
  if (!segment || segment.state !== "saved") {
    showMessage("Only saved path segments can be added to a draft route.");
    return;
  }
  {
    const existingSteps = orderedSelectedDirectionSteps();
    const existingIndex = existingSteps.findIndex(
      (step) => String(step.pathSegmentId) === String(segment.id)
    );
    if (!routeDraftSteps.length && existingIndex >= 0) {
      trimSelectedRouteEnd(segment);
      return;
    }
    if (routeDraftSteps.length && existingIndex >= 0) {
      const currentEnd = routeDraftSteps.at(-1).endJunctionId;
      const touchesCurrentEnd = [
        segmentJunctionId(segment, "start"), segmentJunctionId(segment, "end"),
      ].some((id) => String(id) === String(currentEnd));
      if (touchesCurrentEnd) {
        showMessage("Replacement rejoins the route here. Add it to the edit session, then save changes.", "success");
        renderRouteDraft();
      } else {
        showMessage("Choose a route segment attached to the current replacement end.");
      }
      return;
    }
    const startJunctionId = segmentJunctionId(segment, "start");
    const endJunctionId = segmentJunctionId(segment, "end");
    if (!routeDraftSteps.length) {
      if (existingSteps.length) {
        const routeJunctions = new Set(routeJunctionPositions(existingSteps).map(String));
        const attachments = [startJunctionId, endJunctionId].filter(
          (id) => routeJunctions.has(String(id))
        );
        if (!attachments.length) {
          showMessage("Choose a segment attached to the selected route.");
          return;
        }
        routeReplacementStartJunctionId = attachments[0];
        routeDraftSteps.push(orientedRouteStep(segment, routeReplacementStartJunctionId));
      } else {
        routeDraftSteps.push({ pathSegmentId: segment.id, startJunctionId, endJunctionId });
      }
    } else if (routeDraftSteps.length === 1) {
      const firstSegment = routeSegments()[0];
      const sharedJunctionId = firstSegment ? routeSharedJunction(firstSegment, segment) : null;
      if (!sharedJunctionId) {
        showMessage("Choose a segment connected to either end of the first route segment.");
        return;
      }
      routeDraftSteps[0] = orientedRouteStep(
        firstSegment,
        String(sharedJunctionId) === String(segmentJunctionId(firstSegment, "start"))
          ? segmentJunctionId(firstSegment, "end")
          : segmentJunctionId(firstSegment, "start")
      );
      routeDraftSteps.push(orientedRouteStep(segment, sharedJunctionId));
    } else {
      const currentEndId = String(routeDraftSteps[routeDraftSteps.length - 1].endJunctionId);
      if (String(startJunctionId) === currentEndId) {
        routeDraftSteps.push({
          pathSegmentId: segment.id,
          startJunctionId,
          endJunctionId,
        });
      } else if (String(endJunctionId) === currentEndId) {
        routeDraftSteps.push({
          pathSegmentId: segment.id,
          startJunctionId: endJunctionId,
          endJunctionId: startJunctionId,
        });
      } else {
        showMessage("Choose a segment that starts at the current route end.");
        return;
      }
    }
  }
  hideMessage();
  renderRouteDraft();
}

function clearRouteDraft() {
  routeDraftSteps = [];
  routeReplacementStartJunctionId = null;
  renderRouteDraft();
}

function undoRouteDraftStep() {
  if (!routeDraftSteps.length) return;
  routeDraftSteps.pop();
  if (!routeDraftSteps.length) routeReplacementStartJunctionId = null;
  normalizeSingleRouteStep();
  renderRouteDraft();
}

function exportRouteDraftGpx() {
  if (!routeSegments().length) return;
  const blob = new Blob([routeDraftGpx()], { type: "application/gpx+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "route-atlas-draft.gpx";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function stageBusRouteDraft() {
  const segments = routeSegments();
  const originalDirectionSteps = orderedSelectedDirectionSteps();
  const isRouteReplacement = originalDirectionSteps.length > 0
    && routeReplacementStartJunctionId != null;
  const routeCode = busRouteCode.value.trim();
  if (!editSession || !segments.length || !routeCode || isSaving) return;
  showMessage(`Adding Route ${routeCode}…`, "loading");
  stageBusRouteButton.disabled = true;
  try {
    let route = existingBusRouteForCode();
    let result = editSession;
    let response;
    if (!route) {
      response = await fetch(`/api/edit-sessions/${editSession.token}/routes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ routeCode }),
      });
      result = await response.json();
      if (!response.ok) throw new Error(result.error || "The bus route could not be created.");
      route = result.routes.find(
        (item) => item.state !== "deleted" && item.routeCode.toLowerCase() === routeCode.toLowerCase()
      );
      if (!route) throw new Error("The staged bus route was not returned.");
      editSession = result;
    }
    let direction = (editSession.routeDirections || []).find(
      (item) => String(item.id) === String(busRouteDirection.value)
        && String(item.busRouteId) === String(route.id)
        && item.state !== "deleted"
    );
    if (!direction) {
      response = await fetch(`/api/edit-sessions/${editSession.token}/routes/${encodeURIComponent(route.id)}/directions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: segments[0].routeStartJunctionId,
          endJunctionId: segments[segments.length - 1].routeEndJunctionId,
          customDirectionName: busRouteDirectionName.value.trim() || null,
        }),
      });
      result = await response.json();
      if (!response.ok) throw new Error(result.error || "The route direction could not be created.");
      direction = result.routeDirections.find(
        (item) => item.state !== "deleted" && String(item.busRouteId) === String(route.id)
          && String(item.startJunctionId) === String(segments[0].routeStartJunctionId)
          && String(item.endJunctionId) === String(segments[segments.length - 1].routeEndJunctionId)
      );
      if (!direction) throw new Error("The staged route direction was not returned.");
      editSession = result;
    }
    if (originalDirectionSteps.length && routeReplacementStartJunctionId != null) {
      const positions = routeJunctionPositions(originalDirectionSteps).map(String);
      const startPosition = positions.indexOf(String(routeReplacementStartJunctionId));
      const replacementEndJunctionId = segments.at(-1).routeEndJunctionId;
      const endPosition = positions.indexOf(String(replacementEndJunctionId));
      let removeSteps;
      let replacementStart = originalDirectionSteps[0].startJunctionId;
      let replacementEnd = originalDirectionSteps.at(-1).endJunctionId;
      if (endPosition >= 0 && endPosition !== startPosition) {
        const from = Math.min(startPosition, endPosition);
        const to = Math.max(startPosition, endPosition);
        removeSteps = originalDirectionSteps.slice(from, to);
      } else if (routeOpenEndSide.value === "start") {
        removeSteps = originalDirectionSteps.slice(0, startPosition);
        replacementStart = replacementEndJunctionId;
      } else {
        removeSteps = originalDirectionSteps.slice(startPosition);
        replacementEnd = replacementEndJunctionId;
      }
      for (const step of removeSteps) {
        response = await fetch(
          `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}/segments/${encodeURIComponent(step.pathSegmentId)}`,
          { method: "DELETE" }
        );
        result = await response.json();
        if (!response.ok) throw new Error(result.error || "An old route segment could not be removed.");
      }
      if (endPosition < 0) {
        response = await fetch(
          `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              startJunctionId: replacementStart,
              endJunctionId: replacementEnd,
              customDirectionName: direction.customDirectionName,
            }),
          }
        );
        result = await response.json();
        if (!response.ok) throw new Error(result.error || "The new route terminus could not be saved.");
        const terminus = (result.network?.junctions || []).find(
          (junction) => String(junction.id) === String(replacementEndJunctionId)
        );
        if (terminus) {
          response = await fetch(
            `/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(terminus.id)}/metadata`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                metadata: {
                  ...(terminus.metadata || {}),
                  place_type: "route_terminus",
                  protected: true,
                },
              }),
            }
          );
          result = await response.json();
          if (!response.ok) throw new Error(result.error || "The new Route Terminus could not be protected.");
        }
      }
    }
    for (const segment of segments) {
      const traversal = String(segment.routeStartJunctionId) === String(segmentJunctionId(segment, "start"))
        ? "start_to_end"
        : "end_to_start";
      response = await fetch(
        `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}/segments/${encodeURIComponent(segment.id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ traversal }),
        }
      );
      result = await response.json();
      if (!response.ok) throw new Error(result.error || `Segment ${segment.id} could not be assigned.`);
    }
    renderSession(result);
    routeDraftSteps = [];
    routeReplacementStartJunctionId = null;
    if (isRouteReplacement) {
      busRouteCode.value = routeCode;
      busRoutePicker.value = String(route.id);
      busRouteDirection.value = String(direction.id);
    } else {
      busRoutePicker.value = "";
      busRouteCode.value = "";
      busRouteDirection.replaceChildren();
    }
    renderRouteDraft();
    showMessage(`Segments added to Route ${routeCode}. Use Save changes to commit them.`, "success");
  } catch (error) {
    showMessage(error.message || "The bus route could not be staged.");
    stageBusRouteButton.disabled = false;
  }
}

async function stageBusRouteDirectionName() {
  if (!editSession || isSaving || busRouteDirection.value === "__new__") return;
  const direction = (editSession.routeDirections || []).find(
    (item) => String(item.id) === String(busRouteDirection.value) && item.state !== "deleted"
  );
  if (!direction) return;
  const customDirectionName = busRouteDirectionName.value.trim() || null;
  if ((direction.customDirectionName || null) === customDirectionName) return;
  showMessage("Updating direction name…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/route-directions/${encodeURIComponent(direction.id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: direction.startJunctionId,
          endJunctionId: direction.endJunctionId,
          customDirectionName,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The direction name could not be updated.");
    renderSession(result);
    showMessage("Direction name updated. Use Save changes to commit it.", "success");
  } catch (error) {
    showMessage(error.message || "The direction name could not be updated.");
  }
}

function updateModeInterface() {
  modeButtons.forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.mode === activeMode ? "true" : "false");
  });
  const selected = findSelectedData();
  const hasChanges = Boolean(editSession?.operations.length);
  const showEditHome = activeMode === "edit" && !selected && !hasChanges;
  editHomeSection.hidden = !showEditHome;
  uploadForm.hidden = true;
  cleanupAreaButton.hidden = activeMode !== "edit";
  routeDraftSection.hidden = activeMode !== "route";
  networkSection.hidden = activeMode === "route";
  legendSection.hidden = activeMode === "route";
  sessionSection.hidden = activeMode === "route" || !hasChanges;
}

function setActiveMode(mode) {
  if (!["edit", "route"].includes(mode) || activeMode === mode) return;
  activeMode = mode;

  if (activeMode !== "edit") {
    isAreaCleanupPlacement = false;
    clearAreaCleanupRectangle();
    map.dragging.enable();
    isSplitPlacement = false;
    isJunctionMovePlacement = false;
    traceEditAction = null;
    junctionMergeSourceId = null;
    duplicateCleanupSourceId = null;
    newSegmentDraft = null;
    renderNewSegmentDraft();
  }

  hideMessage();
  renderNetwork(editSession?.network || savedNetwork);
  updateModeInterface();
  updateSelectionInterface();
  updateInterface();
}

function geometryLayers() {
  return [
    ...savedSegmentLayer.getLayers(),
    ...savedJunctionLayer.getLayers(),
    ...addedSegmentLayer.getLayers(),
    ...addedJunctionLayer.getLayers(),
    ...overlapDebugLayer.getLayers(),
    ...overlapReviewLayer.getLayers(),
    ...tracePointLayer.getLayers(),
    ...newSegmentDraftLayer.getLayers(),
    ...routeDraftLayer.getLayers(),
  ];
}

function fitAllPaths() {
  const layers = geometryLayers();
  if (!layers.length) return;
  map.invalidateSize();
  const bounds = L.featureGroup(layers).getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [45, 45], maxZoom: 15 });
}

function fitAllPathsAfterRender() {
  window.requestAnimationFrame(() => fitAllPaths());
}

function junctionPlaceTypeItem(placeType) {
  return (metadataVocabulary.junction_place_type.values || [])
    .find((item) => item.id === placeType) || null;
}

function junctionPlaceTooltip(junction, state) {
  const metadata = junction.metadata || {};
  const placeType = junctionPlaceTypeItem(metadata.place_type);
  const label = placeType?.label;
  const name = metadata.name || label;
  const prefix = state === "added" ? "Added" : "Saved";
  return name ? `${prefix} junction · ${name}` : `${prefix} junction`;
}

function junctionPlaceIconHtml(icon, label) {
  if (icon === "lightrail" || icon === "lightrail.fill") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 17.5V8.25C6 5.9 7.7 4.5 10.1 4.5h3.8c2.4 0 4.1 1.4 4.1 3.75v9.25c0 1.1-.9 2-2 2H8c-1.1 0-2-.9-2-2Z"/><path d="M8 10h8"/><path d="M9 15h.01"/><path d="M15 15h.01"/><path d="m8.75 19.5-1.25 2"/><path d="m15.25 19.5 1.25 2"/></svg>';
  }
  if (icon === "bus.fill") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 16V7.5C5 5.55 6.55 4 8.5 4h7C17.45 4 19 5.55 19 7.5V16c0 1.1-.9 2-2 2H7c-1.1 0-2-.9-2-2Z"/><path d="M7 9h10"/><path d="M8.5 15h.01"/><path d="M15.5 15h.01"/><path d="M8 18v2"/><path d="M16 18v2"/></svg>';
  }
  return `<span>${icon || label.slice(0, 1)}</span>`;
}

function addJunctionPlaceBadge(junction, layer) {
  const metadata = junction.metadata || {};
  const placeType = junctionPlaceTypeItem(metadata.place_type);
  if (!placeType?.id) return;
  const icon = L.divIcon({
    className: "junction-place-badge",
    html: `<div class="junction-place-badge-icon">${junctionPlaceIconHtml(placeType.icon, placeType.label)}</div>`,
    iconSize: [30, 36],
    iconAnchor: [15, 35],
  });
  const marker = L.marker([junction.latitude, junction.longitude], {
    icon,
    interactive: false,
    keyboard: false,
  });
  marker.addTo(layer);
}

function drawJunction(junction) {
  const state = junction.state || "saved";
  const isAdded = state === "added";
  const layer = isAdded ? addedJunctionLayer : savedJunctionLayer;
  const hasPlaceType = Boolean(junction.metadata?.place_type);
  const isProtected = Boolean(junction.metadata?.protected);
  const marker = L.circleMarker([junction.latitude, junction.longitude], {
    radius: ["deleted", "replaced"].includes(state) ? 5 : (hasPlaceType || isProtected ? 5.5 : 4),
    color: ["deleted", "replaced"].includes(state) ? colors[state] : "#ffffff",
    weight: ["deleted", "replaced"].includes(state) ? 2 : (hasPlaceType || isProtected ? 2.5 : 1.5),
    dashArray: ["deleted", "replaced"].includes(state) ? "3 3" : null,
    fillColor: colors[state],
    fillOpacity: ["deleted", "replaced"].includes(state) ? 0.25 : 1,
  });
  marker.bindTooltip(junctionPlaceTooltip(junction, state));
  marker.on("click", (event) => {
    L.DomEvent.stopPropagation(event);
    if (newSegmentDraft) {
      finishNewSegmentDraft(junction);
      return;
    }
    if (isJunctionMovePlacement) {
      if (
        selectedObject?.type === "junction"
        && String(selectedObject.id) !== String(junction.id)
      ) {
        stageJunctionPairMerge(junction.id, selectedObject.id);
        return;
      }
      stageJunctionMove(event.latlng);
      return;
    }
    if (junctionMergeSourceId) {
      stageJunctionPairMerge(junction.id);
      return;
    }
    selectObject("junction", junction.id);
  });
  marker._networkObject = { type: "junction", id: junction.id };
  marker.addTo(layer);
  addJunctionPlaceBadge(junction, layer);
}

function drawPathSegment(segment) {
  const state = segment.state || "saved";
  const isRemoved = ["deleted", "replaced"].includes(state);
  const latLngs = segment.geometry.map(([longitude, latitude, elevation]) =>
    elevation === undefined ? [latitude, longitude] : [latitude, longitude, elevation]
  );
  const isAdded = state === "added";
  const layer = isAdded ? addedSegmentLayer : savedSegmentLayer;
  const lineStyle = visiblePathStyle(segment, state, isAdded, isRemoved);
  if (!isRemoved) drawPositivePreferenceHighlight(segment, latLngs, layer);
  const visibleLine = L.polyline(latLngs, {
    color: lineStyle.color,
    weight: lineStyle.weight,
    opacity: lineStyle.opacity,
    dashArray: lineStyle.dashArray,
    lineCap: "round",
    lineJoin: "round",
    interactive: isRemoved,
  });
  visibleLine._networkBaseStyle = { opacity: visibleLine.options.opacity };
  if (isRemoved) {
    visibleLine.bindTooltip(
      pathSegmentTooltip(segment, state),
      { sticky: true }
    );
  }
  if (!isRemoved) {
    visibleLine.bindTooltip(
      pathSegmentTooltip(segment, state),
      { sticky: true }
    );
  }
  visibleLine._networkObject = { type: "pathSegment", id: segment.id };
  visibleLine.addTo(layer);

  if (!isRemoved) {
    const hitLine = L.polyline(latLngs, {
      color: colors.route,
      weight: 18,
      opacity: 0,
      interactive: true,
    });
    hitLine.bindTooltip(
      pathSegmentTooltip(segment, state),
      { sticky: true }
    );
    hitLine.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      if (isJunctionMovePlacement) {
        stageJunctionMove(event.latlng, segment.id);
        return;
      }
      if (activeMode === "route") {
        toggleRouteSegment(segment);
        return;
      }
      if (newSegmentDraft) {
        finishNewSegmentDraftAtPath(segment, event.latlng);
        return;
      }
      if (
        traceEditAction === "insert"
        && selectedObject?.type === "pathSegment"
        && String(selectedObject.id) === String(segment.id)
      ) {
        stageTracePointInsert(segment, event.latlng);
        return;
      }
      if (
        isSplitPlacement
        && selectedObject?.type === "pathSegment"
        && String(selectedObject.id) === String(segment.id)
      ) {
        stageSplit(segment, event.latlng);
        return;
      }
      selectObject("pathSegment", segment.id);
    });
    hitLine.addTo(layer);
    hitLine.bringToBack();
  }
}

function renderNetwork(network) {
  savedSegmentLayer.clearLayers();
  savedJunctionLayer.clearLayers();
  addedSegmentLayer.clearLayers();
  addedJunctionLayer.clearLayers();
  network.pathSegments.forEach(drawPathSegment);
  network.junctions.forEach(drawJunction);
  renderOverlapDiagnostics();
  renderOverlapReviewLayer();
  renderNewSegmentDraft();
  renderRouteDraft();
  refreshSelection();
}

function renderOverlapDiagnostics() {
  overlapDebugLayer.clearLayers();
  if (!overlapDebugEnabled) return;
  const candidates = editSession?.import?.overlapAnalysis?.candidates || [];
  candidates.forEach((candidate) => {
    const latLngs = candidate.uploadedGeometry.map(
      ([longitude, latitude]) => [latitude, longitude]
    );
    const line = L.polyline(latLngs, {
      color: "#386cb0",
      weight: 8,
      opacity: 0.55,
      dashArray: "4 6",
      interactive: true,
    });
    line.bindTooltip(
      `${candidate.confidence} overlap · ${formatDistance(candidate.lengthMetres)} · `
      + `median ${candidate.medianSeparationMetres} m`,
      { sticky: true }
    );
    line.addTo(overlapDebugLayer);
    [
      [candidate.uploadedStartCoordinate, candidate.startBoundary.type],
      [candidate.uploadedEndCoordinate, candidate.endBoundary.type],
    ].forEach(([coordinate, boundaryType]) => {
      L.circleMarker([coordinate[1], coordinate[0]], {
        radius: 6,
        color: "#ffffff",
        weight: 2,
        fillColor: boundaryType === "branch" || boundaryType === "join"
          ? "#7b3294"
          : "#386cb0",
        fillOpacity: 0.9,
      })
        .bindTooltip(`${boundaryType} boundary`)
        .addTo(overlapDebugLayer);
    });
  });
}

function reviewableOverlapCandidates() {
  return (editSession?.import?.overlapAnalysis?.candidates || [])
    .filter((candidate) => [
      "complete_section_reuse",
      "partial_section_reuse",
    ].includes(candidate.reviewType));
}

function renderOverlapReviewLayer() {
  overlapReviewLayer.clearLayers();
  reviewableOverlapCandidates().forEach((candidate) => {
    const savedIds = new Set(candidate.savedPathSegmentIds.map(String));
    (editSession?.network?.pathSegments || [])
      .filter((segment) => savedIds.has(String(segment.id)))
      .forEach((segment) => {
        L.polyline(
          segment.geometry.map(([longitude, latitude]) => [latitude, longitude]),
          {
            color: colors.saved,
            weight: 10,
            opacity: 0.35,
            interactive: false,
          }
        ).addTo(overlapReviewLayer);
      });
    L.polyline(
      candidate.uploadedGeometry.map(([longitude, latitude]) => [latitude, longitude]),
      {
        color: candidate.decision === "reuse" ? "#386cb0" : "#7b3294",
        weight: 7,
        opacity: candidate.decision ? 0.35 : 0.7,
        dashArray: candidate.decision === "keep" ? "3 6" : null,
        interactive: false,
      }
    ).addTo(overlapReviewLayer);
  });
}

function focusOverlapCandidate(candidate) {
  const bounds = L.latLngBounds(
    candidate.uploadedGeometry.map(([longitude, latitude]) => [latitude, longitude])
  );
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [70, 70], maxZoom: 18 });
}

function focusAdjacentUnresolvedOverlap(direction) {
  const unresolved = reviewableOverlapCandidates()
    .filter((candidate) => !candidate.decision);
  if (!unresolved.length) return;
  const bounds = map.getBounds();
  const visibleIndex = unresolved.findIndex((candidate) =>
    candidate.uploadedGeometry.some(
      ([longitude, latitude]) => bounds.contains([latitude, longitude])
    )
  );
  const baseIndex = visibleIndex === -1 ? (direction > 0 ? -1 : 0) : visibleIndex;
  const nextIndex = (baseIndex + direction + unresolved.length) % unresolved.length;
  focusOverlapCandidate(unresolved[nextIndex]);
}

async function decideOverlapCandidate(candidate, decision) {
  if (!editSession || isSaving) return;
  showMessage(
    decision === "reuse" ? "Reusing the saved path…" : "Keeping the uploaded path…",
    "loading"
  );
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/imports/overlaps/${encodeURIComponent(candidate.key)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The overlap decision could not be applied.");
      return;
    }
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not update the overlap decision.");
  }
}

async function resetOverlapCandidate(candidate) {
  if (!editSession || isSaving) return;
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/imports/overlaps/${encodeURIComponent(candidate.key)}`,
      { method: "DELETE" }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The overlap decision could not be reset.");
      return;
    }
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not reset the overlap decision.");
  }
}

async function adjustOverlapBoundary(latlng) {
  if (!editSession || isSaving || !overlapBoundaryPlacement) return;
  const { candidateKey, boundary } = overlapBoundaryPlacement;
  showMessage("Adjusting the overlap boundary…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/imports/overlaps/${encodeURIComponent(candidateKey)}/boundaries/${boundary}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          longitude: latlng.lng,
          latitude: latlng.lat,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The overlap boundary could not be adjusted.");
      return;
    }
    overlapBoundaryPlacement = null;
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not adjust the overlap boundary.");
  }
}

async function resetOverlapBoundaries(candidate) {
  if (!editSession || isSaving) return;
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/imports/overlaps/${encodeURIComponent(candidate.key)}/boundaries`,
      { method: "DELETE" }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The overlap boundary adjustments could not be reset.");
      return;
    }
    overlapBoundaryPlacement = null;
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not reset the overlap boundary adjustments.");
  }
}

function startOverlapBoundaryPlacement(candidate, boundary) {
  clearActiveToolState();
  overlapBoundaryPlacement = { candidateKey: candidate.key, boundary };
  clearSelection();
  focusOverlapCandidate(candidate);
  hideMessage();
}

function renderOverlapReview() {
  const analysis = editSession?.import?.overlapAnalysis;
  const candidates = reviewableOverlapCandidates();
  overlapSummary.hidden = candidates.length === 0;
  overlapReviewList.replaceChildren();
  if (!analysis || candidates.length === 0) return;

  const summary = analysis.summary;
  overlapSummary.textContent =
    `${formatDistance(summary.reusedDistanceMetres)} reused · `
    + `${formatDistance(summary.newDistanceMetres)} new · `
    + `${summary.unresolvedCount} to review`;
  const unresolved = candidates.filter((candidate) => !candidate.decision);
  const navigator = document.createElement("div");
  navigator.className = "overlap-review-navigation";
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "secondary-button";
  previous.textContent = "Previous unresolved";
  previous.disabled = unresolved.length === 0;
  previous.addEventListener("click", () => focusAdjacentUnresolvedOverlap(-1));
  const next = document.createElement("button");
  next.type = "button";
  next.className = "secondary-button";
  next.textContent = "Next unresolved";
  next.disabled = unresolved.length === 0;
  next.addEventListener("click", () => focusAdjacentUnresolvedOverlap(1));
  navigator.append(previous, next);
  overlapReviewList.replaceChildren(
    navigator,
    ...candidates.map((candidate) => {
      const card = document.createElement("div");
      card.className = `overlap-review-card${candidate.decision ? " is-resolved" : ""}`;
      const heading = document.createElement("strong");
      const isPartial = candidate.reviewType === "partial_section_reuse";
      heading.textContent = candidate.decision === "reuse"
        ? (candidate.decisionSource === "automatic"
          ? "Automatically reusing saved path"
          : "Reusing saved path")
        : (candidate.decision === "keep"
          ? (candidate.decisionSource === "automatic"
            ? "Automatically keeping uploaded path"
            : "Keeping uploaded path")
          : (isPartial
            ? "Possible shared path interval"
            : "Possible complete path overlap"));
      const evidence = document.createElement("span");
      evidence.textContent =
        `${formatDistance(candidate.lengthMetres)} · `
        + `${candidate.confidence} confidence · `
        + `typical separation ${candidate.medianSeparationMetres} m`
        + (isPartial
          ? ` · ${candidate.startBoundary.type} → ${candidate.endBoundary.type}`
          : "");
      const actions = document.createElement("div");
      actions.className = "overlap-review-actions";
      const reuse = document.createElement("button");
      reuse.type = "button";
      reuse.className = "secondary-button";
      reuse.textContent = "Reuse saved path";
      reuse.disabled = candidate.decision === "reuse";
      reuse.addEventListener("click", () => decideOverlapCandidate(candidate, "reuse"));
      const keep = document.createElement("button");
      keep.type = "button";
      keep.className = "secondary-button";
      keep.textContent = "Keep uploaded path";
      keep.disabled = candidate.decision === "keep";
      keep.addEventListener("click", () => decideOverlapCandidate(candidate, "keep"));
      const zoom = document.createElement("button");
      zoom.type = "button";
      zoom.className = "text-button";
      zoom.textContent = "Zoom to overlap";
      zoom.addEventListener("click", () => focusOverlapCandidate(candidate));
      const adjustStart = document.createElement("button");
      adjustStart.type = "button";
      adjustStart.className = "text-button";
      adjustStart.textContent = "Adjust entry";
      adjustStart.addEventListener("click", () => startOverlapBoundaryPlacement(candidate, "start"));
      const adjustEnd = document.createElement("button");
      adjustEnd.type = "button";
      adjustEnd.className = "text-button";
      adjustEnd.textContent = "Adjust exit";
      adjustEnd.addEventListener("click", () => startOverlapBoundaryPlacement(candidate, "end"));
      actions.append(reuse, keep, zoom, adjustStart, adjustEnd);
      if (candidate.hasBoundaryAdjustment) {
        const resetBoundaries = document.createElement("button");
        resetBoundaries.type = "button";
        resetBoundaries.className = "text-button";
        resetBoundaries.textContent = "Reset boundaries";
        resetBoundaries.addEventListener("click", () => resetOverlapBoundaries(candidate));
        actions.append(resetBoundaries);
      }
      if (candidate.decision) {
        const reset = document.createElement("button");
        reset.type = "button";
        reset.className = "text-button";
        reset.textContent = "Review again";
        reset.addEventListener("click", () => resetOverlapCandidate(candidate));
        actions.append(reset);
      }
      card.append(heading, evidence, actions);
      return card;
    })
  );
}

function findSelectedData() {
  if (!selectedObject) return null;
  const network = editSession?.network || savedNetwork;
  const collection = selectedObject.type === "pathSegment"
    ? network.pathSegments
    : network.junctions;
  return collection.find((item) => String(item.id) === String(selectedObject.id)) || null;
}

function junctionCoordinate(junction) {
  const coordinate = [Number(junction.longitude), Number(junction.latitude)];
  if (junction.elevation !== null && junction.elevation !== undefined) {
    coordinate.push(Number(junction.elevation));
  }
  return coordinate;
}

function draftLatLngs() {
  if (!newSegmentDraft) return [];
  return newSegmentDraft.geometry.map(([longitude, latitude]) => [latitude, longitude]);
}

function renderNewSegmentDraft() {
  newSegmentDraftLayer.clearLayers();
  if (!newSegmentDraft) return;
  const latLngs = draftLatLngs();
  if (latLngs.length >= 2) {
    L.polyline(latLngs, {
      color: colors.added,
      weight: 5,
      opacity: 0.75,
      dashArray: "5 7",
      lineCap: "round",
      lineJoin: "round",
      interactive: false,
    }).addTo(newSegmentDraftLayer);
  }
  latLngs.forEach((latlng, index) => {
    L.circleMarker(latlng, {
      radius: index === 0 ? 5 : 4,
      color: "#ffffff",
      weight: 1.5,
      fillColor: index === 0 ? colors.saved : colors.added,
      fillOpacity: 0.95,
      interactive: false,
    }).addTo(newSegmentDraftLayer);
  });
}

function cancelNewSegmentDraft({ hideStatus = true } = {}) {
  newSegmentDraft = null;
  renderNewSegmentDraft();
  if (hideStatus) hideMessage();
  updateSelectionInterface();
}

function startNewSegmentDraft() {
  const junction = findSelectedData();
  if (
    !editSession
    || isSaving
    || selectedObject?.type !== "junction"
    || !junction
    || ["deleted", "replaced"].includes(junction.state)
  ) return;
  clearActiveToolState();
  newSegmentDraft = {
    startJunctionId: junction.id,
    geometry: [junctionCoordinate(junction)],
  };
  renderNewSegmentDraft();
  hideMessage();
  updateSelectionInterface();
}

function addNewSegmentShapePoint(latlng) {
  if (!newSegmentDraft || isSaving) return;
  newSegmentDraft.geometry.push([latlng.lng, latlng.lat]);
  renderNewSegmentDraft();
  updateSelectionInterface();
}

function removeLastNewSegmentShapePoint() {
  if (!newSegmentDraft) return false;
  if (newSegmentDraft.geometry.length <= 1) {
    cancelNewSegmentDraft();
    return true;
  }
  newSegmentDraft.geometry.pop();
  renderNewSegmentDraft();
  updateSelectionInterface();
  return true;
}

async function finishNewSegmentDraft(endJunction) {
  if (!newSegmentDraft || isSaving || !endJunction) return;
  if (String(endJunction.id) === String(newSegmentDraft.startJunctionId)) {
    showMessage("Choose a different junction to finish the new path.");
    return;
  }
  const geometry = [
    ...newSegmentDraft.geometry.map((point) => [...point]),
    junctionCoordinate(endJunction),
  ];
  showMessage("Creating path segment…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: newSegmentDraft.startJunctionId,
          endJunctionId: endJunction.id,
          geometry,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The path segment could not be created.");
      return;
    }
    const createdId = result.createdPathSegment?.pathSegmentId;
    if (createdId) selectedObject = { type: "pathSegment", id: createdId };
    newSegmentDraft = null;
    renderSession(result);
    const duplicateCount = result.createdPathSegment?.duplicateConnectionCount || 0;
    if (duplicateCount) {
      showMessage("Created path segment. These junctions already had a connection.", "success");
    } else {
      hideMessage();
    }
  } catch {
    showMessage("Could not create the path segment.");
  }
}

async function completeNewSegmentAtLastPoint() {
  if (!newSegmentDraft || isSaving) return;
  if (newSegmentDraft.geometry.length < 2) {
    showMessage("Add at least one shape point before completing the path.");
    return;
  }
  const endCoordinate = [...newSegmentDraft.geometry[newSegmentDraft.geometry.length - 1]];
  const geometry = newSegmentDraft.geometry.map((point) => [...point]);
  showMessage("Creating path segment…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: newSegmentDraft.startJunctionId,
          endCoordinate,
          geometry,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The path segment could not be created.");
      return;
    }
    const createdId = result.createdPathSegment?.pathSegmentId;
    if (createdId) selectedObject = { type: "pathSegment", id: createdId };
    newSegmentDraft = null;
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not create the path segment.");
  }
}

async function finishNewSegmentDraftAtPath(segment, latlng) {
  if (!newSegmentDraft || isSaving || !segment) return;
  const geometry = [
    ...newSegmentDraft.geometry.map((point) => [...point]),
    [latlng.lng, latlng.lat],
  ];
  showMessage("Creating path segment…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          startJunctionId: newSegmentDraft.startJunctionId,
          targetPathSegmentId: segment.id,
          endCoordinate: [latlng.lng, latlng.lat],
          geometry,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The path segment could not be created.");
      return;
    }
    const createdId = result.createdPathSegment?.pathSegmentId;
    if (createdId) selectedObject = { type: "pathSegment", id: createdId };
    newSegmentDraft = null;
    renderSession(result);
    if (result.createdPathSegment?.splitTargetPath) {
      showMessage("Created path segment and added a junction on the target path.", "success");
    } else {
      hideMessage();
    }
  } catch {
    showMessage("Could not create the path segment.");
  }
}

function segmentTraceTicks(segment) {
  const geometry = segment.geometry || [];
  if (geometry.length < 2) return [];
  return geometry.map(([longitude, latitude], index) => {
    const previous = geometry[Math.max(0, index - 1)];
    const next = geometry[Math.min(geometry.length - 1, index + 1)];
    const center = map.latLngToLayerPoint([latitude, longitude]);
    const start = map.latLngToLayerPoint([previous[1], previous[0]]);
    const end = map.latLngToLayerPoint([next[1], next[0]]);
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy) || 1;
    const tickLength = 14;
    const normalX = (-dy / length) * tickLength / 2;
    const normalY = (dx / length) * tickLength / 2;
    return [
      map.layerPointToLatLng([center.x - normalX, center.y - normalY]),
      map.layerPointToLatLng([center.x + normalX, center.y + normalY]),
    ];
  });
}

function pathGeometryWithPoint(segment, index, coordinate) {
  return segment.geometry.map((point, pointIndex) =>
    pointIndex === index
      ? [coordinate.lng, coordinate.lat, ...(point.length > 2 ? [point[2]] : [])]
      : [...point]
  );
}

function segmentPointLatLng(segment, index) {
  const point = segment.geometry[index];
  return L.latLng(point[1], point[0]);
}

function closestGeometryInsertion(segment, latlng) {
  const clicked = map.latLngToLayerPoint(latlng);
  const geometry = segment.geometry || [];
  let best = { distance: Infinity, index: 1 };
  for (let index = 0; index < geometry.length - 1; index += 1) {
    const start = map.latLngToLayerPoint([geometry[index][1], geometry[index][0]]);
    const end = map.latLngToLayerPoint([geometry[index + 1][1], geometry[index + 1][0]]);
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const lengthSquared = dx * dx + dy * dy || 1;
    const fraction = Math.max(0, Math.min(1, (
      ((clicked.x - start.x) * dx) + ((clicked.y - start.y) * dy)
    ) / lengthSquared));
    const projected = L.point(start.x + dx * fraction, start.y + dy * fraction);
    const distance = clicked.distanceTo(projected);
    if (distance < best.distance) best = { distance, index: index + 1 };
  }
  return {
    index: best.index,
    coordinate: [latlng.lng, latlng.lat],
  };
}

function renderSelectedTracePoints() {
  tracePointLayer.clearLayers();
  const selected = findSelectedData();
  if (!selected || selectedObject?.type !== "pathSegment") return;
  segmentTraceTicks(selected).forEach((tick, index) => {
    const isSelectedTick = index === selectedTracePointIndex;
    const line = L.polyline(tick, {
      color: "#163f33",
      weight: isSelectedTick ? 4 : 2,
      opacity: isSelectedTick ? 1 : 0.9,
      interactive: true,
      className: `trace-point-tick${isSelectedTick ? " is-selected" : ""}`,
    });
    line.bindTooltip(`Point ${index + 1}`, { sticky: true });
    line.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      const pointLatLng = segmentPointLatLng(selected, index);
      if (newSegmentDraft) {
        finishNewSegmentDraftAtPath(selected, pointLatLng);
        return;
      }
      if (isSplitPlacement) {
        stageSplit(selected, pointLatLng);
        return;
      }
      selectedTracePointIndex = index;
      traceEditAction = null;
      renderSelectedTracePoints();
      updateSelectionInterface();
    });
    line.addTo(tracePointLayer);
  });
}

function refreshSelection() {
  const selected = findSelectedData();
  if (!selected) {
    selectedObject = null;
    isSplitPlacement = false;
    isJunctionMovePlacement = false;
    junctionMergeSourceId = null;
    duplicateCleanupSourceId = null;
  }
  geometryLayers().forEach((layer) => {
    const object = layer._networkObject;
    const isSelected = object
      && selectedObject
      && object.type === selectedObject.type
      && String(object.id) === String(selectedObject.id);
    const element = layer.getElement?.();
    if (element) {
      element.classList.toggle("path-selected", isSelected && object.type === "pathSegment");
      element.classList.toggle("junction-selected", isSelected && object.type === "junction");
    }
    if (
      object?.type === "pathSegment"
      && typeof layer._networkBaseStyle?.opacity === "number"
    ) {
      layer.setStyle({
        opacity: isSelected
          ? selectedOpacityForLayer(layer)
          : layer._networkBaseStyle?.opacity,
      });
    }
  });
  renderSelectedTracePoints();
  updateSelectionInterface();
}

function selectObject(type, id) {
  overlapBoundaryPlacement = null;
  selectedTracePointIndex = null;
  traceEditAction = null;
  selectedObject = { type, id };
  isSplitPlacement = false;
  isJunctionMovePlacement = false;
  isAreaCleanupPlacement = false;
  clearAreaCleanupRectangle();
  map.dragging.enable();
  junctionMergeSourceId = null;
  refreshSelection();
}

function clearSelection() {
  selectedObject = null;
  isSplitPlacement = false;
  isJunctionMovePlacement = false;
  selectedTracePointIndex = null;
  traceEditAction = null;
  junctionMergeSourceId = null;
  duplicateCleanupSourceId = null;
  newSegmentDraft = null;
  renderNewSegmentDraft();
  refreshSelection();
}

function segmentJunctionId(segment, end) {
  return segment[`${end}JunctionId`] ?? segment[`${end}_junction_id`];
}

function stagedJunctionDegree(junctionId) {
  const network = editSession?.network || savedNetwork;
  return network.pathSegments.filter((segment) =>
    ["saved", "added"].includes(segment.state || "saved")
    && [
      segmentJunctionId(segment, "start"),
      segmentJunctionId(segment, "end"),
    ].some((identifier) => String(identifier) === String(junctionId))
  ).length;
}

function incidentPathSegments(junctionId) {
  const network = editSession?.network || savedNetwork;
  return network.pathSegments
    .filter((segment) =>
      ["saved", "added"].includes(segment.state || "saved")
      && [
        segmentJunctionId(segment, "start"),
        segmentJunctionId(segment, "end"),
      ].some((identifier) => String(identifier) === String(junctionId))
    )
    .sort((first, second) =>
      first.distance_m - second.distance_m || String(first.id).localeCompare(String(second.id))
    );
}

function renderJunctionLegTable(junction) {
  const rows = incidentPathSegments(junction.id).map((segment, index) => {
    const row = document.createElement("tr");
    const leg = document.createElement("td");
    const distanceCell = document.createElement("td");
    const state = document.createElement("td");
    const action = document.createElement("td");
    const deleteButton = document.createElement("button");

    leg.textContent = `Leg ${index + 1}`;
    distanceCell.textContent = formatDistance(segment.distance_m);
    state.textContent = segment.state || "saved";
    deleteButton.type = "button";
    deleteButton.className = "leg-delete-button";
    deleteButton.textContent = "Remove";
    deleteButton.addEventListener("click", () => stagePathSegmentDeletion(segment));
    action.append(deleteButton);
    row.append(leg, distanceCell, state, action);
    return row;
  });
  junctionLegList.replaceChildren(...rows);
  junctionLegPanel.hidden = rows.length === 0;
}

function activeToolInfo(isPath, tracePointSelected) {
  if (isPath && traceEditAction === "move") {
    return {
      id: "trace-move",
      name: "Active tool: Move trace point",
      instruction: "Click the map where this trace point should move. Press Esc or click the active tool again to cancel.",
    };
  }
  if (isPath && traceEditAction === "insert") {
    return {
      id: "trace-insert",
      name: "Active tool: Insert trace point",
      instruction: "Click the selected path where the new trace point should be inserted. Press Esc or click the active tool again to cancel.",
    };
  }
  if (isPath && isSplitPlacement) {
    return {
      id: "split",
      name: "Active tool: Add junction",
      instruction: "Click the selected path where the new junction should be placed. Press Esc or click the active tool again to cancel.",
    };
  }
  if (isPath && duplicateCleanupSourceId) {
    return {
      id: "duplicate-cleanup",
      name: "Active tool: Compare duplicate",
      instruction: "Select another saved path to compare as an existing duplicate. Press Esc or click the active tool again to cancel.",
    };
  }
  if (!isPath && newSegmentDraft) {
    return {
      id: "new-segment",
      name: "Active tool: Create path",
      instruction: "Click shape points, then click another junction or path to finish. Press Esc or click the active tool again to cancel.",
    };
  }
  if (!isPath && isJunctionMovePlacement) {
    return {
      id: "junction-move",
      name: "Active tool: Move junction",
      instruction: "Click the map where this junction should move. Press Esc or click the active tool again to cancel.",
    };
  }
  if (!isPath && junctionMergeSourceId) {
    return {
      id: "junction-merge",
      name: "Active tool: Merge junction",
      instruction: "Click the junction that should receive this junction's paths. Press Esc or click the active tool again to cancel.",
    };
  }
  if (isAreaCleanupPlacement) {
    return {
      id: "area-cleanup",
      name: "Active tool: Clean area",
      instruction: "Drag a rectangle around duplicate paths and degree-two junctions. Press Esc or click Clean area again to cancel.",
    };
  }
  if (overlapBoundaryPlacement) {
    return {
      id: "overlap-boundary",
      name: "Active tool: Adjust overlap boundary",
      instruction: "Click the map where the overlap boundary should be placed. Press Esc to cancel.",
    };
  }
  return null;
}

function setActiveToolButton(button, active) {
  button.classList.toggle("is-active-tool", active);
  button.setAttribute("aria-pressed", active ? "true" : "false");
}

function renderActiveToolPanel(tool) {
  activeToolPanel.hidden = !tool;
  if (!tool) {
    activeToolName.textContent = "";
    activeToolInstruction.textContent = "";
    return;
  }
  activeToolName.textContent = tool.name;
  activeToolInstruction.textContent = tool.instruction;
}

function updateSelectionInterface() {
  const selected = findSelectedData();
  const selectionMode = activeMode === "edit";
  updateModeInterface();
  selectionSection.hidden = !selected || !selectionMode;
  const isPlacement = isSplitPlacement
    || isJunctionMovePlacement
    || isAreaCleanupPlacement
    || Boolean(traceEditAction)
    || Boolean(junctionMergeSourceId)
    || Boolean(overlapBoundaryPlacement)
    || Boolean(duplicateCleanupSourceId)
    || Boolean(newSegmentDraft);
  map.getContainer().classList.toggle("split-placement", isPlacement);
  cleanupAreaButton.classList.toggle("is-active", isAreaCleanupPlacement);
  cleanupAreaButton.setAttribute("aria-pressed", isAreaCleanupPlacement ? "true" : "false");
  selectionCard.classList.toggle("is-placement", isPlacement);
  if (!selected) {
    renderActiveToolPanel(null);
    pathActionGroup.hidden = true;
    junctionActionGroup.hidden = true;
    traceEditPanel.hidden = true;
    metadataPanel.hidden = true;
    junctionLegPanel.hidden = true;
    junctionLegList.replaceChildren();
    return;
  }

  const isPath = selectedObject.type === "pathSegment";
  const tracePointSelected = isPath && selectedTracePointIndex !== null;
  const activeTool = activeToolInfo(isPath, tracePointSelected);
  const junctionDegree = isPath ? 0 : stagedJunctionDegree(selected.id);
  renderActiveToolPanel(activeTool);
  selectionName.textContent = tracePointSelected
    ? `Trace point ${selectedTracePointIndex + 1}`
    : (isPath ? "Path segment" : "Junction");
  selectionDetails.textContent = tracePointSelected
    ? `${selected.state} path · point ${selectedTracePointIndex + 1} of ${selected.geometry.length}`
    : (isPath
      ? `${selected.state} · ${formatDistance(selected.distance_m)}`
      : `${selected.state} junction · degree ${junctionDegree}`);
  const isInactive = ["deleted", "replaced"].includes(selected.state);
  const isEditMode = activeMode === "edit";
  pathActionGroup.hidden = !isEditMode || !isPath || isInactive || tracePointSelected;
  junctionActionGroup.hidden = !isEditMode || isPath || isInactive;
  metadataPanel.hidden = !isEditMode || isInactive || tracePointSelected;
  if (!metadataPanel.hidden) renderMetadataForm(selected, selectedObject.type);
  splitModeButton.hidden = !isPath || ["deleted", "replaced"].includes(selected.state);
  setButtonLabel(splitModeButton, isSplitPlacement ? "Adding junction..." : "Add junction", "A");
  setActiveToolButton(splitModeButton, activeTool?.id === "split");
  deleteSegmentButton.hidden = !isPath || ["deleted", "replaced"].includes(selected.state);
  setButtonLabel(deleteSegmentButton, "Delete", "Backspace");
  setActiveToolButton(deleteSegmentButton, false);
  duplicateCleanupButton.hidden = true;
  setActiveToolButton(duplicateCleanupButton, activeTool?.id === "duplicate-cleanup");
  traceInsertButton.hidden = !isPath || ["deleted", "replaced"].includes(selected.state);
  setButtonLabel(traceInsertButton, traceEditAction === "insert" ? "Inserting trace point..." : "Insert trace point", "I");
  setActiveToolButton(traceInsertButton, activeTool?.id === "trace-insert");
  traceEditPanel.hidden = !isEditMode || !tracePointSelected || ["deleted", "replaced"].includes(selected.state);
  const canEditSelectedPoint = isPath
    && selectedTracePointIndex !== null
    && selectedTracePointIndex > 0
    && selectedTracePointIndex < selected.geometry.length - 1;
  traceMoveButton.disabled = !canEditSelectedPoint;
  traceDeleteButton.disabled = !canEditSelectedPoint || selected.geometry.length <= 2;
  setButtonLabel(traceMoveButton, traceEditAction === "move" ? "Moving point..." : "Move point", "M");
  setActiveToolButton(traceMoveButton, activeTool?.id === "trace-move");
  setButtonLabel(traceDeleteButton, "Delete point", "Backspace");
  setActiveToolButton(traceDeleteButton, false);
  cleanJunctionButton.hidden = true;
  setButtonLabel(cleanJunctionButton, "Clean around junction");
  setActiveToolButton(cleanJunctionButton, false);
  newSegmentButton.hidden = !isEditMode || isPath || ["deleted", "replaced"].includes(selected.state);
  setButtonLabel(newSegmentButton, newSegmentDraft ? "Drawing path..." : "Create new path", "N");
  setActiveToolButton(newSegmentButton, activeTool?.id === "new-segment");
  completeNewSegmentButton.hidden = !isEditMode || isPath || ["deleted", "replaced"].includes(selected.state) || !newSegmentDraft;
  completeNewSegmentButton.disabled = !newSegmentDraft || newSegmentDraft.geometry.length < 2;
  finishNewPathButton.hidden = !newSegmentDraft;
  finishNewPathButton.disabled = !newSegmentDraft || newSegmentDraft.geometry.length < 2;
  setButtonLabel(completeNewSegmentButton, "Save path at new junction", "Enter");
  setActiveToolButton(completeNewSegmentButton, false);
  moveJunctionButton.hidden = !isEditMode || isPath || ["deleted", "replaced"].includes(selected.state);
  setButtonLabel(moveJunctionButton, isJunctionMovePlacement
    ? "Moving junction..."
    : "Move/reconnect junction",
    "M"
  );
  setActiveToolButton(moveJunctionButton, activeTool?.id === "junction-move");
  mergeJunctionsButton.hidden = true;
  setActiveToolButton(mergeJunctionsButton, activeTool?.id === "junction-merge");
  mergeJunctionButton.hidden = !isEditMode
    || isPath
    || ["deleted", "replaced"].includes(selected.state)
    || junctionDegree >= 3;
  mergeJunctionButton.disabled = !isPath && ![1, 2].includes(junctionDegree);
  setButtonLabel(
    mergeJunctionButton,
    junctionDegree === 1 ? "Delete junction and leg" : "Delete and merge",
    "Backspace"
  );
  setActiveToolButton(mergeJunctionButton, false);
  junctionLegPanel.hidden = isPath
    || ["deleted", "replaced"].includes(selected.state)
    || junctionDegree < 3;
  if (isPath || ["deleted", "replaced"].includes(selected.state) || junctionDegree < 3) {
    junctionLegList.replaceChildren();
  } else {
    renderJunctionLegTable(selected);
  }
  if (isPath && traceEditAction === "move") {
    splitModeNote.textContent = "Click the map where the selected trace point should move.";
  } else if (isPath && traceEditAction === "insert") {
    splitModeNote.textContent = "Click the selected path where the new trace point should be inserted.";
  } else if (tracePointSelected) {
    splitModeNote.textContent = "Move or delete this interior trace point.";
  } else if (isPath && isSplitPlacement) {
    splitModeNote.textContent = "Click the selected path where the new junction should be placed.";
  } else if (isPath && duplicateCleanupSourceId) {
    splitModeNote.textContent = "Select another saved path to compare as an existing duplicate.";
  } else if (isPath) {
    splitModeNote.textContent = "The click will be projected precisely onto this path.";
  } else if (newSegmentDraft) {
    splitModeNote.textContent = "Click shape points, click another junction or path, or complete at the last point.";
  } else if (isJunctionMovePlacement) {
    splitModeNote.textContent = "Click the map at the junction's new position. Attached paths will follow.";
  } else if (junctionMergeSourceId) {
    splitModeNote.textContent = "Click the junction that should receive this junction's paths.";
  } else if (junctionDegree === 1) {
    splitModeNote.textContent = "Delete removes the leaf junction and its single leg.";
  } else if (junctionDegree === 2) {
    splitModeNote.textContent = "Move it, or remove it and join its two incident paths.";
  } else {
    splitModeNote.textContent = "Move it, create a path, or delete individual legs below.";
  }
}

function updateInterface() {
  const network = editSession?.network || savedNetwork;
  const savedSegments = network.pathSegments.filter((segment) => segment.state !== "added").length;
  const savedJunctions = network.junctions.filter((junction) => junction.state !== "added").length;
  const hasChanges = Boolean(editSession?.operations.length);
  const hasGeometry = geometryLayers().length > 0;

  networkCounts.textContent = savedSegments === 0
    ? "No saved paths yet"
    : `${savedSegments} ${savedSegments === 1 ? "path segment" : "path segments"} · `
      + `${savedJunctions} ${savedJunctions === 1 ? "junction" : "junctions"}`;
  sessionSection.hidden = activeMode === "route" || !hasChanges;
  sessionEmptyState.hidden = true;
  sessionSummary.hidden = true;
  undoButton.disabled = !editSession?.canUndo;
  fileInput.disabled = hasChanges || isSaving;
  cleanupAreaButton.disabled = isSaving || !savedSegments;
  dropZone.classList.toggle("disabled", hasChanges || isSaving);
  fitButton.hidden = !hasGeometry;
  mapOverlay.classList.toggle("has-routes", hasGeometry);
  sessionRevision.textContent = editSession ? editSession.baseRevision.slice(0, 8) : "";

  if (editSession?.isStale) {
    saveNote.textContent = "The saved network changed. Cancel and restart this session.";
    saveNote.classList.add("is-warning");
  } else if (editSession?.import?.overlapAnalysis?.hasUnresolvedOverlaps) {
    saveNote.textContent = "Review every proposed path overlap before saving.";
    saveNote.classList.add("is-warning");
  } else {
    saveNote.textContent = "Save applies the complete staged change set.";
    saveNote.classList.remove("is-warning");
  }
  saveButton.disabled = !editSession?.canCommit || isSaving;
  undoButton.disabled = !editSession?.canUndo || isSaving;
  cancelSessionButton.disabled = isSaving;
  panelCleanupAreaButton.disabled = cleanupAreaButton.disabled;

  const addedCount = editSession?.changeSummary.addedPathSegments || 0;
  const replacedCount = editSession?.changeSummary.replacedPathSegments || 0;
  const deletedCount = editSession?.changeSummary.deletedPathSegments || 0;
  const statusParts = [];
  if (savedSegments) statusParts.push(`${savedSegments} saved`);
  if (addedCount) statusParts.push(`${addedCount} added`);
  if (replacedCount) statusParts.push(`${replacedCount} replaced`);
  if (deletedCount) statusParts.push(`${deletedCount} deleted`);
  mapStatus.textContent = statusParts.length ? statusParts.join(" · ") : "Waiting for paths";

  renderOverlapReview();
  renderRouteDraft();
  updateModeInterface();
  if (!hasChanges) return;
  const summary = editSession.changeSummary;
  operationName.textContent = editSession.import?.name || "Staged changes";
  changeCounts.textContent = [
    `${summary.addedPathSegments} added paths`,
    `${summary.addedJunctions} added junctions`,
    `${summary.replacedPathSegments} replaced paths`,
    `${summary.deletedPathSegments} deleted paths`,
    `${summary.deletedJunctions} deleted junctions`,
  ].join(" · ");
  if (editSession.import) {
    const stats = editSession.import.stats;
    operationStats.textContent =
      `${formatDistance(stats.distanceMeters)} · `
      + `↗ ${stats.elevationGainMeters.toLocaleString()} m · `
      + `${stats.pointCount.toLocaleString()} pts`;
  } else {
    operationStats.textContent = "";
  }
  const duplicateCount = editSession.skippedDuplicates.length;
  duplicateSummary.hidden = duplicateCount === 0;
  duplicateSummary.textContent = duplicateCount
    ? `${duplicateCount} exact ${duplicateCount === 1 ? "duplicate was" : "duplicates were"} skipped`
    : "";
  operationList.replaceChildren();
}

function renderSession(session, { fit = false } = {}) {
  editSession = session;
  renderNetwork(session.network);
  updateInterface();
  if (fit) fitAllPathsAfterRender();
}

function clearAreaCleanupRectangle() {
  if (areaCleanupRectangle) {
    areaCleanupRectangle.remove();
    areaCleanupRectangle = null;
  }
  areaCleanupStartLatLng = null;
}

function setAreaCleanupPlacement(enabled) {
  clearActiveToolState();
  isAreaCleanupPlacement = enabled;
  if (!enabled) {
    clearAreaCleanupRectangle();
    map.dragging.enable();
  }
  cleanupAreaButton.classList.toggle("is-active", isAreaCleanupPlacement);
  cleanupAreaButton.setAttribute("aria-pressed", isAreaCleanupPlacement ? "true" : "false");
  updateSelectionInterface();
  updateInterface();
}

function toggleAreaCleanupPlacement() {
  if (isSaving || cleanupAreaButton.disabled) return;
  setAreaCleanupPlacement(!isAreaCleanupPlacement);
  if (isAreaCleanupPlacement) {
    hideMessage();
  } else {
    hideMessage();
  }
}

function cleanupBoundsPayload(bounds) {
  return {
    minLongitude: bounds.getWest(),
    minLatitude: bounds.getSouth(),
    maxLongitude: bounds.getEast(),
    maxLatitude: bounds.getNorth(),
  };
}

function boundsAroundCoordinate(latitude, longitude, radiusMetres) {
  const latitudePadding = radiusMetres / 111_320;
  const longitudePadding = radiusMetres
    / (111_320 * Math.max(Math.cos(latitude * Math.PI / 180), 0.000001));
  return L.latLngBounds(
    [latitude - latitudePadding, longitude - longitudePadding],
    [latitude + latitudePadding, longitude + longitudePadding]
  );
}

async function stageAreaCleanup(bounds, description = "selected area") {
  if (!editSession || isSaving) return;
  showMessage(`Cleaning up ${description}…`, "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/duplicate-cleanups/area`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bounds: cleanupBoundsPayload(bounds) }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The selected area could not be cleaned up.");
      return;
    }
    const cleanup = result.areaCleanup || {};
    renderSession(result);
    showMessage(
      `Staged ${cleanup.stagedCount || 0} cleanup ${cleanup.stagedCount === 1 ? "operation" : "operations"} `
      + `(${cleanup.duplicateCleanupCount || 0} duplicate paths, `
      + `${cleanup.stubDeleteCount || 0} short stubs, `
      + `${cleanup.junctionMergeCount || 0} degree-two junctions).`
      + (cleanup.skippedCount ? ` Skipped ${cleanup.skippedCount} uncertain candidates.` : ""),
      "success"
    );
  } catch {
    showMessage(`Could not clean up the ${description}.`);
  }
}

function stageSelectedJunctionCleanup() {
  const junction = findSelectedData();
  if (
    !editSession
    || isSaving
    || selectedObject?.type !== "junction"
    || !junction
    || ["deleted", "replaced"].includes(junction.state)
  ) return;

  const bounds = boundsAroundCoordinate(
    Number(junction.latitude),
    Number(junction.longitude),
    junctionCleanupRadiusMetres
  );
  stageAreaCleanup(bounds, "around the selected junction");
}

async function stageTraceGeometry(segment, geometry) {
  showMessage("Updating trace points…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(segment.id)}/geometry`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ geometry }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The trace points could not be updated.");
      return;
    }
    const editedSegment = result.network.pathSegments.find(
      (segment) => segment.state === "added" && segment.origin === "geometry_edit"
    );
    if (editedSegment) selectedObject = { type: "pathSegment", id: editedSegment.id };
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not update the trace points.");
  }
}

async function stageMetadata() {
  if (metadataStageTimer) {
    clearTimeout(metadataStageTimer);
    metadataStageTimer = null;
  }
  const selected = findSelectedData();
  if (
    !editSession
    || isSaving
    || !["pathSegment", "junction"].includes(selectedObject?.type)
    || !selected
    || ["deleted", "replaced"].includes(selected.state)
  ) return;

  const isPath = selectedObject.type === "pathSegment";
  if (!metadataChanged(selected, selectedObject.type)) return;
  const endpoint = isPath
    ? `/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(selected.id)}/metadata`
    : `/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(selected.id)}/metadata`;
  try {
    const response = await fetch(
      endpoint,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          metadata: isPath ? readPathMetadataForm() : readJunctionMetadataForm(),
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The metadata could not be updated.");
      return;
    }
    if (isPath) {
      const editedSegment = result.network.pathSegments.find(
        (segment) => segment.state === "added" && segment.origin === "metadata_edit"
      );
      if (editedSegment) selectedObject = { type: "pathSegment", id: editedSegment.id };
    }
    renderSession(result);
  } catch {
    showMessage("Could not update the metadata.");
  }
}

function scheduleMetadataStage({ delay = 0 } = {}) {
  if (metadataStageTimer) clearTimeout(metadataStageTimer);
  metadataStageTimer = setTimeout(() => {
    stageMetadata();
  }, delay);
}

async function flushMetadataStage() {
  if (!metadataStageTimer) return;
  await stageMetadata();
}

function cancelPendingMetadataStage() {
  if (!metadataStageTimer) return;
  clearTimeout(metadataStageTimer);
  metadataStageTimer = null;
}

function stageTracePointMove(latlng) {
  const segment = findSelectedData();
  if (
    !editSession
    || !segment
    || selectedObject?.type !== "pathSegment"
    || selectedTracePointIndex === null
    || selectedTracePointIndex <= 0
    || selectedTracePointIndex >= segment.geometry.length - 1
  ) return;
  traceEditAction = null;
  const geometry = pathGeometryWithPoint(segment, selectedTracePointIndex, latlng);
  stageTraceGeometry(segment, geometry);
}

function stageTracePointDelete() {
  const segment = findSelectedData();
  if (
    !editSession
    || !segment
    || selectedObject?.type !== "pathSegment"
    || selectedTracePointIndex === null
    || selectedTracePointIndex <= 0
    || selectedTracePointIndex >= segment.geometry.length - 1
    || segment.geometry.length <= 2
  ) return;
  const geometry = segment.geometry.filter((_point, index) => index !== selectedTracePointIndex);
  selectedTracePointIndex = Math.min(selectedTracePointIndex, geometry.length - 2);
  traceEditAction = null;
  stageTraceGeometry(segment, geometry);
}

function stageTracePointInsert(segment, latlng) {
  if (!editSession || !segment || selectedObject?.type !== "pathSegment") return;
  const insertion = closestGeometryInsertion(segment, latlng);
  const geometry = [
    ...segment.geometry.slice(0, insertion.index).map((point) => [...point]),
    insertion.coordinate,
    ...segment.geometry.slice(insertion.index).map((point) => [...point]),
  ];
  selectedTracePointIndex = insertion.index;
  traceEditAction = null;
  stageTraceGeometry(segment, geometry);
}

async function stageSplit(segment, latlng) {
  if (!editSession || isSaving) return;
  showMessage("Adding the junction split…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(segment.id)}/split`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          longitude: latlng.lng,
          latitude: latlng.lat,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The path could not be split.");
      return;
    }
    if (result.selectedObject) {
      selectedObject = result.selectedObject;
      isSplitPlacement = false;
      renderSession(result);
      showMessage("That position uses the existing endpoint junction.", "success");
      return;
    }
    selectedObject = null;
    isSplitPlacement = false;
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not add the junction.");
  }
}

async function stagePathSegmentDeletion(segment = findSelectedData()) {
  if (
    !editSession
    || isSaving
    || !segment
    || ["deleted", "replaced"].includes(segment.state)
  ) return;

  showMessage("Staging path-segment deletion…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/path-segments/${encodeURIComponent(segment.id)}`,
      { method: "DELETE" }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The path segment could not be deleted.");
      return;
    }
    if (
      selectedObject?.type === "pathSegment"
      && String(selectedObject.id) === String(segment.id)
    ) {
      clearSelection();
    }
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not stage the path-segment deletion.");
  }
}

async function stageDuplicateCleanup() {
  const selected = findSelectedData();
  if (
    !editSession
    || isSaving
    || selectedObject?.type !== "pathSegment"
    || !selected
    || selected.state !== "saved"
  ) return;
  if (!duplicateCleanupSourceId || String(duplicateCleanupSourceId) === String(selected.id)) {
    clearActiveToolState();
    duplicateCleanupSourceId = selected.id;
    hideMessage();
    updateSelectionInterface();
    return;
  }

  showMessage("Comparing duplicate path candidates…", "loading");
  try {
    const compareResponse = await fetch(
      `/api/edit-sessions/${editSession.token}/duplicate-cleanups/compare`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          firstPathSegmentId: duplicateCleanupSourceId,
          secondPathSegmentId: selected.id,
          retainedPathSegmentId: duplicateCleanupSourceId,
        }),
      }
    );
    const comparison = await compareResponse.json();
    if (!compareResponse.ok) {
      showMessage(comparison.error || "The duplicate paths could not be compared.");
      return;
    }
    const conflictText = comparison.metadataConflict
      ? " Metadata differs; the first path's metadata will be retained."
      : "";
    const confirmed = window.confirm(
      `Clean up duplicate path?\n\n`
      + `Keep the first selected path and remove the current path.\n`
      + `Maximum separation: ${comparison.maximumSeparationMetres} m.\n`
      + `Length ratio: ${comparison.lengthRatio} · traversal: ${comparison.traversalDirection}.\n`
      + `External connections to reconnect: ${comparison.externalConnectionCount}.`
      + conflictText
    );
    if (!confirmed) {
      hideMessage();
      return;
    }
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/duplicate-cleanups`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          retainedPathSegmentId: duplicateCleanupSourceId,
          removedPathSegmentId: selected.id,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The duplicate cleanup could not be staged.");
      return;
    }
    duplicateCleanupSourceId = null;
    clearSelection();
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not stage the duplicate cleanup.");
  }
}

async function stageJunctionMerge() {
  const junction = findSelectedData();
  const degree = junction ? stagedJunctionDegree(junction.id) : 0;
  if (
    !editSession
    || isSaving
    || selectedObject?.type !== "junction"
    || !junction
    || ["deleted", "replaced"].includes(junction.state)
    || ![1, 2].includes(degree)
  ) return;

  if (degree === 1) {
    const [segment] = incidentPathSegments(junction.id);
    if (!segment) return;
    await stagePathSegmentDeletion(segment);
    clearSelection();
    return;
  }

  showMessage("Staging junction removal and path merge…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(junction.id)}`,
      { method: "DELETE" }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The junction could not be removed.");
      return;
    }
    clearSelection();
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not stage the junction merge.");
  }
}

async function stageJunctionPairMerge(targetJunctionId, sourceJunctionId = junctionMergeSourceId) {
  if (!editSession || isSaving || !sourceJunctionId) return;
  if (String(sourceJunctionId) === String(targetJunctionId)) {
    showMessage("Choose a different junction to merge into.");
    return;
  }

  showMessage("Merging the selected junctions…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(sourceJunctionId)}/merge`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ targetJunctionId }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The junctions could not be merged.");
      return;
    }
    clearSelection();
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not stage the junction merge.");
  }
}

async function stageJunctionMove(latlng, targetPathSegmentId = null) {
  const junction = findSelectedData();
  if (
    !editSession
    || isSaving
    || !isJunctionMovePlacement
    || selectedObject?.type !== "junction"
    || !junction
    || ["deleted", "replaced"].includes(junction.state)
  ) return;

  showMessage(
    targetPathSegmentId
      ? "Moving the junction onto the path and splitting it…"
      : "Moving the junction and attached paths…",
    "loading"
  );
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/junctions/${encodeURIComponent(junction.id)}/move`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          longitude: latlng.lng,
          latitude: latlng.lat,
          targetPathSegmentId,
        }),
      }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The junction could not be moved.");
      return;
    }
    clearSelection();
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not stage the junction move.");
  }
}

async function loadSavedNetwork() {
  const response = await fetch("/api/path-network");
  if (!response.ok) throw new Error("Path network request failed.");
  savedNetwork = await response.json();
}

async function loadMetadataVocabulary() {
  try {
    const response = await fetch("/static/metadata-vocabulary.json");
    if (!response.ok) return;
    metadataVocabulary = await response.json();
  } catch {
    // The default in-code vocabulary keeps the editor usable offline.
  }
}

async function createEditSession({ fit = false } = {}) {
  const response = await fetch("/api/edit-sessions", { method: "POST" });
  if (!response.ok) throw new Error("Edit session request failed.");
  renderSession(await response.json(), { fit });
}

async function initialize() {
  try {
    await loadMetadataVocabulary();
    await loadSavedNetwork();
    await createEditSession({ fit: true });
  } catch {
    networkCounts.textContent = "Saved paths could not be loaded";
    showMessage("Could not initialize the path-network editor.");
  }
}

async function uploadFile(file) {
  if (!file) {
    showMessage("Please choose one GPX file.");
    return;
  }
  if (!editSession || editSession.operations.length) {
    showMessage("Undo or Cancel the current import before uploading another GPX file.");
    return;
  }

  const formData = new FormData();
  formData.append("file", file, file.name);
  showMessage("Adding the GPX import operation…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/imports`,
      { method: "POST", body: formData }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The GPX import could not be staged.");
      return;
    }
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not reach the server. Please try again.");
  } finally {
    fileInput.value = "";
  }
}

async function undoLastOperation() {
  if (!editSession?.canUndo) return;
  cancelPendingMetadataStage();
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/undo`,
      { method: "POST" }
    );
    const result = await response.json();
    if (!response.ok) {
      showMessage(result.error || "The last operation could not be undone.");
      return;
    }
    clearSelection();
    renderSession(result);
    hideMessage();
  } catch {
    showMessage("Could not undo the last operation.");
  }
}

async function saveCurrentSession() {
  if (!editSession?.canCommit || isSaving) return;
  await flushMetadataStage();
  isSaving = true;
  updateInterface();
  showMessage("Saving staged changes…", "loading");
  try {
    const response = await fetch(
      `/api/edit-sessions/${editSession.token}/commit`,
      { method: "POST" }
    );
    const result = await response.json();
    if (!response.ok) {
      if (
        response.status === 409
        && (result.error || "").includes("saved path network changed")
      ) {
        editSession = { ...editSession, isStale: true, canCommit: false };
        updateInterface();
      }
      showMessage(result.error || "The staged changes could not be saved.");
      return;
    }
    savedNetwork = result.network;
    clearSelection();
    renderSession(result);
    showMessage("Changes saved.", "success");
  } catch {
    showMessage("Could not save the staged changes.");
  } finally {
    isSaving = false;
    updateInterface();
  }
}

async function cancelCurrentSession() {
  if (!editSession) return;
  if (editSession.operations.length && !window.confirm("Discard all staged changes?")) return;
  cancelPendingMetadataStage();
  const token = editSession.token;
  try {
    const response = await fetch(`/api/edit-sessions/${token}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error("Cancel failed.");
    clearSelection();
    await createEditSession();
    hideMessage();
  } catch {
    showMessage("The edit session could not be cancelled.");
  }
}

function toggleSplitPlacement() {
  const shouldActivate = !isSplitPlacement;
  clearActiveToolState();
  isSplitPlacement = shouldActivate;
  updateSelectionInterface();
}

function toggleTracePointMove() {
  if (traceMoveButton.disabled) return;
  const shouldActivate = traceEditAction !== "move";
  clearActiveToolState();
  traceEditAction = shouldActivate ? "move" : null;
  updateSelectionInterface();
}

function toggleTracePointInsert() {
  const shouldActivate = traceEditAction !== "insert";
  clearActiveToolState();
  traceEditAction = shouldActivate ? "insert" : null;
  updateSelectionInterface();
}

function toggleJunctionMovePlacement() {
  const shouldActivate = !isJunctionMovePlacement;
  clearActiveToolState();
  isJunctionMovePlacement = shouldActivate;
  updateSelectionInterface();
}

function toggleJunctionPairMergePlacement() {
  const junction = findSelectedData();
  if (!junction || selectedObject?.type !== "junction") return;
  const shouldActivate = !junctionMergeSourceId;
  clearActiveToolState();
  junctionMergeSourceId = shouldActivate ? junction.id : null;
  updateSelectionInterface();
}

function toggleNewSegmentDraft() {
  if (newSegmentDraft) {
    cancelNewSegmentDraft();
    return;
  }
  startNewSegmentDraft();
}

function cancelActiveActionOrSelection() {
  const hadActiveAction = isSplitPlacement
    || isJunctionMovePlacement
    || isAreaCleanupPlacement
    || Boolean(traceEditAction)
    || Boolean(junctionMergeSourceId)
    || Boolean(overlapBoundaryPlacement)
    || Boolean(duplicateCleanupSourceId)
    || Boolean(newSegmentDraft);

  if (!hadActiveAction) {
    clearSelection();
    return;
  }

  clearActiveToolState();
  updateSelectionInterface();
  updateInterface();
  hideMessage();
}

function isEditableShortcutTarget(target) {
  return Boolean(
    target?.closest?.("input, textarea, select, [contenteditable='true']")
  );
}

function handleKeyboardShortcut(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || isEditableShortcutTarget(event.target)) {
    return;
  }

  const selected = findSelectedData();
  const selectedType = selectedObject?.type;
  const key = event.key.toLowerCase();

  if (key === "escape") {
    event.preventDefault();
    cancelActiveActionOrSelection();
    return;
  }

  if (key === "backspace" || key === "delete") {
    if (newSegmentDraft) {
      event.preventDefault();
      removeLastNewSegmentShapePoint();
      return;
    }
    if (!selected || activeMode !== "edit") return;
    event.preventDefault();
    if (
      selectedType === "pathSegment"
      && selectedTracePointIndex !== null
    ) {
      stageTracePointDelete();
    } else if (selectedType === "pathSegment") {
      stagePathSegmentDeletion(selected);
    } else if (selectedType === "junction") {
      stageJunctionMerge();
    }
    return;
  }

  if (key === "a" && activeMode === "edit" && selectedType === "pathSegment" && selected) {
    event.preventDefault();
    toggleSplitPlacement();
    return;
  }

  if (key === "enter" && newSegmentDraft) {
    event.preventDefault();
    if (newSegmentDraft.geometry.length >= 2) {
      completeNewSegmentAtLastPoint();
    } else {
      hideMessage();
    }
    return;
  }

  if (key === "i" && activeMode === "edit" && selectedType === "pathSegment" && selected) {
    event.preventDefault();
    toggleTracePointInsert();
    return;
  }

  if (key === "m" && activeMode === "edit") {
    if (selectedType === "junction" && selected) {
      event.preventDefault();
      toggleJunctionMovePlacement();
    } else if (
      selectedType === "pathSegment"
      && selected
      && selectedTracePointIndex !== null
    ) {
      event.preventDefault();
      toggleTracePointMove();
    }
  }

  if (key === "n" && activeMode === "edit" && selectedType === "junction" && selected) {
    event.preventDefault();
    toggleNewSegmentDraft();
  }
}

fileInput.addEventListener("change", (event) => uploadFile(event.target.files?.[0]));
["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!fileInput.disabled) dropZone.classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});
dropZone.addEventListener("drop", (event) => {
  if (!fileInput.disabled) uploadFile(event.dataTransfer?.files?.[0]);
});
undoButton.addEventListener("click", undoLastOperation);
saveButton.addEventListener("click", saveCurrentSession);
cancelSessionButton.addEventListener("click", cancelCurrentSession);
fitButton.addEventListener("click", fitAllPaths);
cleanupAreaButton.addEventListener("click", toggleAreaCleanupPlacement);
panelCleanupAreaButton.addEventListener("click", toggleAreaCleanupPlacement);
addJunctionButton.addEventListener("click", toggleAddJunctionPlacement);
clearSelectionButton.addEventListener("click", clearSelection);
undoRouteSegmentButton.addEventListener("click", undoRouteDraftStep);
clearRouteButton.addEventListener("click", clearRouteDraft);
exportRouteGpxButton.addEventListener("click", exportRouteDraftGpx);
stageBusRouteButton.addEventListener("click", stageBusRouteDraft);
saveRouteChangesButton.addEventListener("click", saveCurrentSession);
busRouteCode.addEventListener("input", () => {
  renderRouteDraft();
  if (existingBusRouteForCode()) fitExistingBusRoute();
});
busRoutePicker.addEventListener("change", () => {
  const route = (editSession?.routes || []).find(
    (item) => String(item.id) === String(busRoutePicker.value) && item.state !== "deleted"
  );
  if (route) {
    busRouteCode.value = route.routeCode;
  } else {
    busRouteCode.value = "";
  }
  renderRouteDraft();
  if (route) fitExistingBusRoute();
});
busRouteDirection.addEventListener("change", () => {
  renderRouteDraft();
  fitExistingBusRoute();
});
busRouteDirectionName.addEventListener("change", stageBusRouteDirectionName);
splitModeButton.addEventListener("click", toggleSplitPlacement);
deleteSegmentButton.addEventListener("click", () => stagePathSegmentDeletion());
duplicateCleanupButton.addEventListener("click", stageDuplicateCleanup);
traceMoveButton.addEventListener("click", toggleTracePointMove);
traceInsertButton.addEventListener("click", toggleTracePointInsert);
traceDeleteButton.addEventListener("click", stageTracePointDelete);
metadataPreference.addEventListener("change", () => scheduleMetadataStage());
metadataNotes.addEventListener("input", () => scheduleMetadataStage({ delay: 600 }));
metadataNotes.addEventListener("blur", () => stageMetadata());
junctionPlaceType.addEventListener("change", () => {
  const isRouteTerminus = junctionPlaceType.value === "route_terminus";
  if (isRouteTerminus) junctionProtected.checked = true;
  junctionProtected.disabled = isRouteTerminus;
  scheduleMetadataStage();
});
junctionName.addEventListener("input", () => scheduleMetadataStage({ delay: 600 }));
junctionName.addEventListener("blur", () => stageMetadata());
junctionNotes.addEventListener("input", () => scheduleMetadataStage({ delay: 600 }));
junctionNotes.addEventListener("blur", () => stageMetadata());
junctionProtected.addEventListener("change", () => scheduleMetadataStage());
cleanJunctionButton.addEventListener("click", stageSelectedJunctionCleanup);
newSegmentButton.addEventListener("click", toggleNewSegmentDraft);
completeNewSegmentButton.addEventListener("click", completeNewSegmentAtLastPoint);
finishNewPathButton.addEventListener("click", completeNewSegmentAtLastPoint);
moveJunctionButton.addEventListener("click", toggleJunctionMovePlacement);
mergeJunctionsButton.addEventListener("click", toggleJunctionPairMergePlacement);
mergeJunctionButton.addEventListener("click", stageJunctionMerge);
document.addEventListener("keydown", handleKeyboardShortcut);
modeButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveMode(button.dataset.mode));
});
map.on("mousedown", (event) => {
  if (!isAreaCleanupPlacement || isSaving) return;
  areaCleanupStartLatLng = event.latlng;
  map.dragging.disable();
  if (areaCleanupRectangle) areaCleanupRectangle.remove();
  areaCleanupRectangle = L.rectangle(
    L.latLngBounds(areaCleanupStartLatLng, areaCleanupStartLatLng),
    {
      color: "#1f6b4f",
      weight: 2,
      fillColor: "#2d9b69",
      fillOpacity: 0.12,
      className: "cleanup-selection-rectangle",
    }
  ).addTo(map);
  L.DomEvent.preventDefault(event.originalEvent);
});
map.on("mousemove", (event) => {
  if (!isAreaCleanupPlacement || !areaCleanupStartLatLng || !areaCleanupRectangle) return;
  areaCleanupRectangle.setBounds(L.latLngBounds(areaCleanupStartLatLng, event.latlng));
});
map.on("mouseup", (event) => {
  if (!isAreaCleanupPlacement || !areaCleanupStartLatLng || !areaCleanupRectangle) return;
  const bounds = L.latLngBounds(areaCleanupStartLatLng, event.latlng);
  const northWest = map.latLngToLayerPoint(bounds.getNorthWest());
  const southEast = map.latLngToLayerPoint(bounds.getSouthEast());
  const width = Math.abs(southEast.x - northWest.x);
  const height = Math.abs(southEast.y - northWest.y);
  ignoreNextMapClick = true;
  setAreaCleanupPlacement(false);
  if (width < 8 || height < 8) {
    showMessage("Drag a larger cleanup area.");
    return;
  }
  stageAreaCleanup(bounds);
});
map.on("zoomend moveend", () => {
  renderSelectedTracePoints();
  if (activeMode === "route") renderRouteDraftLayer();
});
map.on("zoomend", () => {
  if (activeMode === "route") renderNetwork(editSession?.network || savedNetwork);
});
map.on("click", (event) => {
  if (ignoreNextMapClick) {
    ignoreNextMapClick = false;
    return;
  }
  if (isAreaCleanupPlacement) return;
  if (isAddJunctionPlacement) {
    placeStandaloneJunction(event.latlng);
    return;
  }
  if (newSegmentDraft) {
    addNewSegmentShapePoint(event.latlng);
    return;
  }
  if (overlapBoundaryPlacement) {
    adjustOverlapBoundary(event.latlng);
    return;
  }
  if (traceEditAction === "move") {
    stageTracePointMove(event.latlng);
    return;
  }
  if (isJunctionMovePlacement) {
    stageJunctionMove(event.latlng);
    return;
  }
  if (activeMode === "edit" && !isSplitPlacement) clearSelection();
});

updateInterface();
initialize();
