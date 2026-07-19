const mapNode = document.querySelector("#map");
const map = L.map(mapNode).setView([51.5074, -0.1278], 12);
L.tileLayer(mapNode.dataset.tileUrl, {
  attribution: mapNode.dataset.attribution,
  maxZoom: Number(mapNode.dataset.maxZoom),
}).addTo(map);

const networkGreen = "#1f6b4f";
const selectedRed = "#e13f32";
const lines = L.layerGroup().addTo(map);
const nodes = L.layerGroup().addTo(map);
let data;
let selectedRoute = null;
let selectedDirection = null;
let selectedSegment = null;

const colour = (route) => route?.colour || selectedRed;

function selectedSegmentIds() {
  if (!selectedRoute) return new Set();
  return new Set((data.routeMemberships || [])
    .filter((membership) => selectedDirection
      ? String(membership.routeDirectionId) === String(selectedDirection)
      : selectedRoute.directions.some(
        (direction) => String(direction.id) === String(membership.routeDirectionId)
      ))
    .map((membership) => String(membership.pathSegmentId)));
}

function junctionTooltip(junction) {
  const metadata = junction.metadata || {};
  const placeLabel = metadata.place_type === "route_terminus" ? "Route Terminus" : "";
  return metadata.name || placeLabel || "Junction";
}

function addJunctionBadge(junction) {
  if (junction.metadata?.place_type !== "route_terminus") return;
  const icon = L.divIcon({
    className: "junction-place-badge",
    html: '<div class="junction-place-badge-icon"><span>T</span></div>',
    iconSize: [30, 36],
    iconAnchor: [15, 35],
  });
  L.marker([junction.latitude, junction.longitude], {
    icon,
    interactive: false,
    keyboard: false,
  }).addTo(nodes);
}

function render() {
  lines.clearLayers();
  nodes.clearLayers();
  const activeIds = selectedSegmentIds();
  for (const segment of data.pathSegments) {
    const active = activeIds.has(String(segment.id))
      || String(selectedSegment?.id) === String(segment.id);
    L.polyline(segment.geometry.map((point) => [point[1], point[0]]), {
      color: active ? selectedRed : networkGreen,
      weight: active ? 6 : 4.5,
      opacity: active ? 1 : 0.82,
      lineCap: "round",
      lineJoin: "round",
      bubblingMouseEvents: false,
    }).on("click", () => selectSegment(segment)).addTo(lines);
  }
  for (const junction of data.junctions) {
    const metadata = junction.metadata || {};
    const emphasized = Boolean(metadata.place_type || metadata.protected);
    L.circleMarker([junction.latitude, junction.longitude], {
      radius: emphasized ? 5.5 : 4,
      color: "#ffffff",
      weight: emphasized ? 2.5 : 1.5,
      fillColor: networkGreen,
      fillOpacity: 1,
    }).bindTooltip(junctionTooltip(junction)).addTo(nodes);
    addJunctionBadge(junction);
  }
}

function updateDirections() {
  const field = document.querySelector("#direction-field");
  const select = document.querySelector("#direction-filter");
  field.hidden = !selectedRoute;
  if (!selectedRoute) return;
  select.innerHTML = '<option value="">All directions</option>'
    + selectedRoute.directions.map(
      (direction) => `<option value="${direction.id}">${direction.displayName || "Undefined direction"}</option>`
    ).join("");
  select.value = selectedDirection || "";
}

function routes() {
  const query = document.querySelector("#route-search").value.toLowerCase();
  const list = document.querySelector("#route-list");
  list.innerHTML = "";
  for (const route of data.routes.filter((item) => item.routeCode.toLowerCase().includes(query))) {
    const button = document.createElement("button");
    button.className = `route-item${selectedRoute?.id === route.id ? " active" : ""}`;
    button.innerHTML = `<span class="route-badge" style="background:${colour(route)}">${route.routeCode}</span>`
      + `<span>${route.displayName || route.directions.map((direction) => direction.displayName || "Direction not defined").join(" · ")}</span>`;
    button.onclick = () => {
      selectedRoute = selectedRoute?.id === route.id ? null : route;
      selectedDirection = null;
      selectedSegment = null;
      document.querySelector("#selection").hidden = true;
      updateDirections();
      render();
      routes();
      if (selectedRoute) fitRoute(route);
    };
    list.append(button);
  }
}

function fitRoute(route) {
  const ids = new Set(data.routeMemberships
    .filter((membership) => selectedDirection
      ? String(membership.routeDirectionId) === String(selectedDirection)
      : route.directions.some(
        (direction) => String(direction.id) === String(membership.routeDirectionId)
      ))
    .map((membership) => String(membership.pathSegmentId)));
  const points = data.pathSegments
    .filter((segment) => ids.has(String(segment.id)))
    .flatMap((segment) => segment.geometry.map((point) => [point[1], point[0]]));
  if (points.length) map.fitBounds(points, { padding: [30, 30] });
}

function selectSegment(segment) {
  selectedRoute = null;
  selectedDirection = null;
  selectedSegment = segment;
  updateDirections();
  render();
  routes();
  const panel = document.querySelector("#selection");
  panel.hidden = false;
  document.querySelector("#selection-title").textContent = `Segment ${segment.id}`;
  document.querySelector("#selection-detail").innerHTML = segment.routeMemberships.length
    ? segment.routeMemberships.map(
      (membership) => `<div class="membership-line"><span class="route-badge" style="background:${membership.colour || selectedRed}">${membership.routeCode}</span> ${membership.directionName || ""}</div>`
    ).join("")
    : "No bus routes use this segment.";
}

function clearSelection() {
  selectedRoute = null;
  selectedDirection = null;
  selectedSegment = null;
  updateDirections();
  document.querySelector("#selection").hidden = true;
  render();
  routes();
  history.replaceState(null, "", "/");
}

document.querySelector("#route-search").oninput = routes;
document.querySelector("#direction-filter").onchange = (event) => {
  selectedDirection = event.target.value || null;
  render();
  if (selectedRoute) fitRoute(selectedRoute);
};
map.on("click", clearSelection);

fetch("/api/public/network").then((response) => response.json()).then((body) => {
  data = body;
  render();
  routes();
  const params = new URLSearchParams(location.search);
  const code = params.get("route");
  if (code) {
    selectedRoute = data.routes.find(
      (route) => route.routeCode.toLowerCase() === code.toLowerCase()
    ) || null;
    const direction = params.get("direction");
    if (selectedRoute && direction) {
      const match = selectedRoute.directions.find(
        (item) => (item.displayName || "").toLowerCase().replace("bound", "")
          === direction.toLowerCase().replace("bound", "")
      );
      selectedDirection = match?.id || null;
    }
    updateDirections();
    render();
    routes();
    if (selectedRoute) fitRoute(selectedRoute);
  }
  if (data.bounds) {
    map.fitBounds([
      [data.bounds[1], data.bounds[0]],
      [data.bounds[3], data.bounds[2]],
    ]);
  }
});
