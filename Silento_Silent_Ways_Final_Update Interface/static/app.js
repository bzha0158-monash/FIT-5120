// ============================================================
// 1. MAP CONFIGURATION AND APPLICATION STATE
// ============================================================
// The app is intentionally limited to Melbourne CBD so search,
// routing and sensor overlays remain focused on the project area.
const CBD_BOUNDS = L.latLngBounds([-37.826, 144.945], [-37.797, 144.979]);
const CBD_CENTER = [-37.8136, 144.9631];
const ROUTE_COLORS = ["#1f8a70", "#5f6ad4", "#c6862a"];

const map = L.map("map", {
  center: CBD_CENTER,
  zoom: 15,
  minZoom: 13,
  maxBounds: CBD_BOUNDS.pad(0.18),
  zoomControl: false,
  dragging: true
});

L.control.zoom({ position: "bottomright" }).addTo(map);
map.createPane("crowdCoveragePane");
map.getPane("crowdCoveragePane").style.zIndex = 410;
map.getPane("crowdCoveragePane").style.pointerEvents = "auto";
map.createPane("safeSpacePane");
map.getPane("safeSpacePane").style.zIndex = 430;

const lightTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 20,
  attribution: "© OpenStreetMap contributors"
});
const darkTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 20,
  attribution: "© OpenStreetMap contributors © CARTO"
});
lightTiles.addTo(map);

let sensors = [];
let safeSpaces = [];
let crowdLayer = L.layerGroup();
let safeSpaceLayer = L.layerGroup();
let routeAlternativesLayer = L.layerGroup().addTo(map);
let endpointLayer = L.layerGroup().addTo(map);
let routeCandidates = [];
let selectedRouteIndex = -1;
let startPoint = null;
let destinationPoint = null;
let currentRouteCoordinates = [];
let routeReady = false;
// Overlay visibility is user-controlled. Route switching preserves these choices.
let showSafeSpaces = false;
let showCrowdAlerts = false;

const startInput = document.getElementById("startInput");
const destinationInput = document.getElementById("destinationInput");
const startResultsBox = document.getElementById("startSearchResults");
const destinationResultsBox = document.getElementById("destinationSearchResults");
const routeSummary = document.getElementById("routeSummary");
const routeActions = document.getElementById("routeActions");
const legend = document.getElementById("legend");
const routeOptions = document.getElementById("routeOptions");

// ============================================================
// 2. SMALL UI HELPERS AND CUSTOM MAP ICONS
// ============================================================
function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 2800);
}

function safeSpaceIcon() {
  // Match the top-left Silent Waze brand: solid green tile with a white S.
  return L.divIcon({
    className: "safe-space-icon-wrapper",
    html: '<div class="safe-space-marker brand-safe-marker" aria-hidden="true">S</div>',
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -16]
  });
}

function endpointIcon(kind = "start") {
  return L.divIcon({
    className: "endpoint-icon-wrapper",
    html: `<div class="endpoint-marker ${kind}" aria-hidden="true">${kind === "start" ? "A" : "B"}</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 28],
    popupAnchor: [0, -18]
  });
}

// Load crowd conditions and potential sensory-refuge locations
// from the Flask API once when the page starts. Raw CSV files are
// processed by the backend; the browser only receives JSON.
async function loadData() {
  try {
    const [crowdResponse, safeResponse] = await Promise.all([
      fetch("/api/crowd"),
      fetch("/api/safe-spaces")
    ]);
    if (!crowdResponse.ok || !safeResponse.ok) {
      throw new Error("Dataset API request failed.");
    }
    sensors = await crowdResponse.json();
    safeSpaces = await safeResponse.json();
  } catch (error) {
    console.error(error);
    showToast("Could not load the original local datasets.");
  }
}

// ============================================================
// 3. ADDRESS SEARCH AND START/DESTINATION SELECTION
// ============================================================
// Debouncing avoids sending a geocoding request after every keystroke.
function debounce(fn, delay = 450) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
}

function resultBoxFor(target) {
  return target === "start" ? startResultsBox : destinationResultsBox;
}

function hideSearchResults(target) {
  resultBoxFor(target).classList.add("hidden");
}

async function searchLocation(query, target) {
  const resultsBox = resultBoxFor(target);
  if (!query || query.trim().length < 3) {
    resultsBox.classList.add("hidden");
    resultsBox.innerHTML = "";
    return;
  }

  const boundedView = "144.945,-37.797,144.979,-37.826";
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=6&countrycodes=au&viewbox=${boundedView}&bounded=1&q=${encodeURIComponent(query + ", Melbourne VIC")}`;
  try {
    const response = await fetch(url, { headers: { "Accept-Language": "en" } });
    if (!response.ok) throw new Error("Search request failed");
    const results = await response.json();
    renderSearchResults(results, target);
  } catch (error) {
    console.error(error);
    showToast("Location search is unavailable. Check your internet connection.");
  }
}

function renderSearchResults(results, target) {
  const resultsBox = resultBoxFor(target);
  resultsBox.innerHTML = "";
  if (!results.length) {
    resultsBox.innerHTML = '<div class="result-empty">No Melbourne CBD result found.</div>';
  } else {
    results.forEach(result => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "result-item";
      item.innerHTML = `<strong>${result.name || result.display_name.split(",")[0]}</strong><span>${result.display_name}</span>`;
      item.addEventListener("click", () => selectSearchResult(result, target));
      resultsBox.appendChild(item);
    });
  }
  resultsBox.classList.remove("hidden");
}

function selectSearchResult(result, target) {
  const point = L.latLng(Number(result.lat), Number(result.lon));
  const fullAddress = result.display_name;
  if (target === "start") {
    startPoint = point;
    startInput.value = fullAddress;
  } else {
    destinationPoint = point;
    destinationInput.value = fullAddress;
  }
  map.flyTo(point, 16);
  hideSearchResults(target);
}

startInput.addEventListener("input", debounce(event => {
  startPoint = null;
  searchLocation(event.target.value, "start");
}));
destinationInput.addEventListener("input", debounce(event => {
  destinationPoint = null;
  searchLocation(event.target.value, "destination");
}));

startInput.addEventListener("focus", () => {
  if (startResultsBox.children.length) startResultsBox.classList.remove("hidden");
});
destinationInput.addEventListener("focus", () => {
  if (destinationResultsBox.children.length) destinationResultsBox.classList.remove("hidden");
});

document.addEventListener("click", event => {
  if (!event.target.closest(".search-field")) {
    startResultsBox.classList.add("hidden");
    destinationResultsBox.classList.add("hidden");
  }
});

// Use browser geolocation only for the starting point. The CBD
// boundary check prevents accidental routing far outside the demo area.
function useCurrentLocation() {
  if (!navigator.geolocation) {
    showToast("Location access is not supported by this browser.");
    return;
  }
  navigator.geolocation.getCurrentPosition(position => {
    const point = L.latLng(position.coords.latitude, position.coords.longitude);
    if (!CBD_BOUNDS.pad(0.25).contains(point)) {
      showToast("Your current location is outside the Melbourne CBD demo area.");
      return;
    }
    startPoint = point;
    startInput.value = "Current location";
    map.flyTo(point, 16);
    const reverseUrl = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${point.lat}&lon=${point.lng}&zoom=18&addressdetails=1`;
    fetch(reverseUrl, { headers: { "Accept-Language": "en" } })
      .then(response => response.ok ? response.json() : null)
      .then(result => {
        if (result?.display_name) startInput.value = result.display_name;
      })
      .catch(() => {});
  }, () => showToast("Location permission was not granted."));
}

document.getElementById("useLocationButton").addEventListener("click", useCurrentLocation);

// ============================================================
// 4. SPATIAL HELPERS FOR ROUTE-NEARBY FEATURES
// ============================================================
// These functions measure how close sensors and safe spaces are
// to any segment of a candidate route.
function distanceToSegment(point, start, end) {
  const x = point.lng;
  const y = point.lat;
  const x1 = start.lng;
  const y1 = start.lat;
  const x2 = end.lng;
  const y2 = end.lat;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lengthSquared = dx * dx + dy * dy;
  let t = lengthSquared === 0 ? 0 : ((x - x1) * dx + (y - y1) * dy) / lengthSquared;
  t = Math.max(0, Math.min(1, t));
  return map.distance(point, L.latLng(y1 + t * dy, x1 + t * dx));
}

function distanceToRoute(point, routeCoordinates) {
  let minimum = Number.POSITIVE_INFINITY;
  for (let i = 0; i < routeCoordinates.length - 1; i += 1) {
    minimum = Math.min(minimum, distanceToSegment(point, routeCoordinates[i], routeCoordinates[i + 1]));
  }
  return minimum;
}

function sensorsNearRoute(routeCoordinates, thresholdMetres = 80) {
  return sensors.filter(sensor => {
    const point = L.latLng(Number(sensor.Latitude), Number(sensor.Longitude));
    return distanceToRoute(point, routeCoordinates) <= thresholdMetres;
  });
}

function safeSpacesNearRoute(routeCoordinates, thresholdMetres = 380) {
  return safeSpaces
    .map(place => ({
      ...place,
      routeDistance: distanceToRoute(
        L.latLng(Number(place.Latitude), Number(place.Longitude)),
        routeCoordinates
      )
    }))
    .filter(place => place.routeDistance <= thresholdMetres)
    .sort((a, b) => a.routeDistance - b.routeDistance)
    .slice(0, 14);
}

function crowdStyle(status) {
  const styles = {
    low: { color: "#2f8f78", fillColor: "#45a98d", fillOpacity: 0.10 },
    medium: { color: "#c47b14", fillColor: "#e6a235", fillOpacity: 0.12 },
    high: { color: "#ad3447", fillColor: "#d65364", fillOpacity: 0.14 }
  };
  return styles[status] || styles.low;
}

// ============================================================
// 5. ROUTE SENSORY INDICATOR
// ============================================================
// Route-level High/Low is based on predicted crowd categories
// from sensors close to that route. No percentage is displayed.
function evaluateSensoryIndicator(routeCoordinates) {
  const nearbySensors = sensorsNearRoute(routeCoordinates, 80)
    .filter(sensor => sensor.status !== "no_data");

  const highCount = nearbySensors.filter(sensor => sensor.status === "high").length;
  const mediumCount = nearbySensors.filter(sensor => sensor.status === "medium").length;
  const lowCount = nearbySensors.filter(sensor => sensor.status === "low").length;
  const sensorCount = nearbySensors.length;

  if (sensorCount === 0) {
    return { level: "low", highCount, mediumCount, lowCount, sensorCount };
  }

  const level = (highCount * 2 + mediumCount) > sensorCount ? "high" : "low";
  return { level, highCount, mediumCount, lowCount, sensorCount };
}

function routeLabel(index) {
  return `Route ${index + 1}`;
}

function recommendedRouteIndex() {
  if (!routeCandidates.length) return -1;
  const ranked = routeCandidates
    .map((item, index) => ({
      index,
      highPenalty: item.sensory.level === "high" ? 1 : 0,
      highCount: item.sensory.highCount,
      mediumCount: item.sensory.mediumCount,
      time: item.route.summary.totalTime
    }))
    .sort((a, b) =>
      a.highPenalty - b.highPenalty ||
      a.highCount - b.highCount ||
      a.mediumCount - b.mediumCount ||
      a.time - b.time
    );
  return ranked[0].index;
}

function routeColor(index) {
  return ROUTE_COLORS[index % ROUTE_COLORS.length];
}

// ============================================================
// 6. ROUTE VISUALISATION AND ROUTE-CARD INTERACTION
// ============================================================
// Each of the three routes keeps a fixed colour so users can match
// the map line to the corresponding card on the right.
function drawRouteAlternatives() {
  routeAlternativesLayer.clearLayers();
  const recommended = recommendedRouteIndex();

  routeCandidates.forEach((item, index) => {
    const selected = index === selectedRouteIndex;
    const color = routeColor(index);
    const line = L.polyline(item.route.coordinates, {
      color,
      opacity: selected ? 0.98 : 0.48,
      weight: selected ? 7 : 5,
      dashArray: selected ? null : "8 9",
      lineCap: "round",
      lineJoin: "round"
    });
    line.bindTooltip(`${routeLabel(index)} · ${item.sensory.level.toUpperCase()} sensory${index === recommended ? " · recommended" : ""}`, {
      sticky: true,
      direction: "top"
    });
    line.on("click", () => selectRoute(index, false));
    line.addTo(routeAlternativesLayer);

    if (selected) {
      L.polyline(item.route.coordinates, {
        color,
        opacity: 0.16,
        weight: 14,
        interactive: false,
        lineCap: "round",
        lineJoin: "round"
      }).addTo(routeAlternativesLayer);
      line.bringToFront();
    }
  });
}

function renderRouteOptions() {
  if (!routeOptions) return;
  routeOptions.innerHTML = "";
  const recommended = recommendedRouteIndex();

  if (routeCandidates.length <= 1) {
    const note = document.createElement("p");
    note.className = "route-options-note";
    note.textContent = "The walking router returned one route for these locations.";
    routeOptions.appendChild(note);
  }

  routeCandidates.forEach((item, index) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `route-option route-${index + 1} ${index === selectedRouteIndex ? "selected" : ""}`;
    // Keep the route title on one line. The Recommended badge sits above
    // the title so it does not squeeze labels such as "Route 3".
    card.innerHTML = `
      <span class="route-swatch" aria-hidden="true"></span>
      <span class="route-copy">
        ${index === recommended ? '<em class="recommended-badge">★ Recommended</em>' : ""}
        <span class="route-option-main">
          <strong>${routeLabel(index)}</strong>
        </span>
        <span class="route-option-meta">
          ${(item.route.summary.totalDistance / 1000).toFixed(1)} km · ${Math.round(item.route.summary.totalTime / 60)} min
        </span>
      </span>
      <span class="sensory-badge sensory-${item.sensory.level}">
        Sensory ${item.sensory.level === "high" ? "High" : "Low"}
      </span>
    `;
    card.addEventListener("click", () => selectRoute(index, true));
    routeOptions.appendChild(card);
  });
}

function selectRoute(index, fitRoute = false) {
  if (index < 0 || index >= routeCandidates.length) return;
  selectedRouteIndex = index;
  const selected = routeCandidates[index];
  currentRouteCoordinates = selected.route.coordinates;
  routeReady = true;

  drawRouteAlternatives();
  renderRouteOptions();
  const nearbySensors = showRouteLayers(selected.route.coordinates);
  updateRouteSummary(selected.route, nearbySensors, selected.sensory);

  if (fitRoute) {
    map.fitBounds(L.latLngBounds(selected.route.coordinates), { padding: [70, 70] });
  }
}

// ============================================================
// 7. CROWD COVERAGE AND SAFE-SPACE OVERLAYS
// ============================================================
// Only features near the currently selected route are drawn.
function drawCrowdCoverage(routeCoordinates) {
  crowdLayer.clearLayers();
  const nearbySensors = sensorsNearRoute(routeCoordinates);

  nearbySensors.forEach(sensor => {
    // The map only visualises High alerts. Low/Medium remain in the
    // data and are still used by the route sensory algorithm.
    if (sensor.status !== "high") return;
    const position = [Number(sensor.Latitude), Number(sensor.Longitude)];
    const radius = Number(sensor.coverage_radius || 55);
    const style = crowdStyle(sensor.status);

    const coverage = L.circle(position, {
      pane: "crowdCoveragePane",
      radius,
      className: `crowd-zone crowd-zone-${sensor.status}`,
      weight: 2,
      ...style
    });

    const pulse = L.circle(position, {
      pane: "crowdCoveragePane",
      radius: radius * 0.72,
      className: `crowd-pulse crowd-pulse-${sensor.status}`,
      color: style.color,
      fillColor: style.fillColor,
      fillOpacity: 0.05,
      opacity: 0.45,
      weight: 2,
      interactive: false
    });

    const currentText = sensor.current_count == null ? "0" : `${Math.round(Number(sensor.current_count))}`;
    const forecastText = sensor.expected_count == null ? "No historical baseline" : `${Math.round(Number(sensor.expected_count))}`;
    const currentLevelText = sensor.current_level === "no_data" ? "Unavailable" : sensor.current_level;
    const forecastLevelText = sensor.forecast_level === "no_data" ? "Unavailable" : sensor.forecast_level;

    coverage.bindPopup(`
      <strong>${sensor.Sensor_Description || "Pedestrian sensor"}</strong><br>
      Current 60-minute total: ${currentText} (${currentLevelText})<br>
      Next-hour estimate: ${forecastText} (${forecastLevelText})<br>
      Predicted crowd level: ${sensor.status}
    `);

    // Hover shows the alert details automatically; click still works as a fallback.
    coverage.on("mouseover", function () { this.openPopup(); });
    coverage.on("mouseout", function () { this.closePopup(); });

    pulse.addTo(crowdLayer);
    coverage.addTo(crowdLayer);
  });
  return nearbySensors;
}

function drawSafeSpaces(routeCoordinates) {
  safeSpaceLayer.clearLayers();
  const nearbyPlaces = safeSpacesNearRoute(routeCoordinates);
  nearbyPlaces.forEach(place => {
    const marker = L.marker([Number(place.Latitude), Number(place.Longitude)], {
      pane: "safeSpacePane",
      icon: safeSpaceIcon()
    });
    marker.bindPopup(`
      <strong>${place["Feature Name"]}</strong><br>
      ${place["Sub Theme"] || place.Theme || "Potential refuge location"}<br>
      About ${Math.round(place.routeDistance)} m from the route
    `);
    marker.on("mouseover", function () { this.openPopup(); });
    marker.on("mouseout", function () { this.closePopup(); });
    marker.addTo(safeSpaceLayer);
  });
  return nearbyPlaces;
}

function showRouteLayers(routeCoordinates) {
  currentRouteCoordinates = routeCoordinates;
  const nearbySensors = drawCrowdCoverage(routeCoordinates);
  drawSafeSpaces(routeCoordinates);

  // Re-apply the user's current overlay choices after changing routes.
  if (showCrowdAlerts) {
    crowdLayer.addTo(map);
    legend.classList.remove("hidden");
  } else {
    if (map.hasLayer(crowdLayer)) map.removeLayer(crowdLayer);
    legend.classList.add("hidden");
  }

  if (showSafeSpaces) {
    safeSpaceLayer.addTo(map);
  } else if (map.hasLayer(safeSpaceLayer)) {
    map.removeLayer(safeSpaceLayer);
  }

  routeActions.classList.remove("hidden");
  document.getElementById("safeSpacesButton").classList.toggle("active", showSafeSpaces);
  document.getElementById("crowdButton").classList.toggle("active", showCrowdAlerts);
  return nearbySensors;
}

// Update the right-side summary for the currently selected route.
// Recommendation order is sensory level, fewer High sensors, fewer
// Medium sensors, then shorter walking time.
function updateRouteSummary(route, nearbySensors, sensory) {
  const highAlerts = nearbySensors.filter(sensor => sensor.status === "high");
  const mediumAlerts = nearbySensors.filter(sensor => sensor.status === "medium");

  document.getElementById("distanceValue").textContent = `${(route.summary.totalDistance / 1000).toFixed(1)} km`;
  document.getElementById("durationValue").textContent = `${Math.round(route.summary.totalTime / 60)} min`;
  document.getElementById("alertValue").textContent = highAlerts.length;
  document.getElementById("sensoryValue").textContent = sensory?.level === "high" ? "High" : "Low";
  document.getElementById("sensoryValue").className = sensory?.level === "high" ? "sensory-text-high" : "sensory-text-low";

  const message = document.getElementById("alertMessage");
  if (highAlerts.length) {
    const names = highAlerts.slice(0, 3).map(item => item.Sensor_Description).join(", ");
    message.textContent = `High predicted crowd coverage appears near ${names}. The recommended route is chosen by sensory level first, then fewer High sensors, fewer Medium sensors, and finally shorter walking time.`;
  } else if (mediumAlerts.length) {
    message.textContent = `No high-level warning is predicted on this route, although some sections may have moderate pedestrian activity. The recommended route is still chosen by lower sensory burden first.`;
  } else {
    message.textContent = `The available sensors indicate relatively low predicted crowd conditions along this route. The recommended route is chosen by lower sensory burden first, then fewer busy sensors and shorter walking time.`;
  }
  routeSummary.classList.remove("hidden");
  document.body.classList.add("route-view");
  document.querySelector(".search-panel")?.classList.add("hidden");
}

function resetRouteLayers() {
  crowdLayer.clearLayers();
  safeSpaceLayer.clearLayers();
  if (map.hasLayer(crowdLayer)) map.removeLayer(crowdLayer);
  if (map.hasLayer(safeSpaceLayer)) map.removeLayer(safeSpaceLayer);
  routeActions.classList.add("hidden");
  legend.classList.add("hidden");
  routeSummary.classList.add("hidden");
  document.body.classList.remove("route-view");
  currentRouteCoordinates = [];
  routeCandidates = [];
  selectedRouteIndex = -1;
  routeAlternativesLayer.clearLayers();
  endpointLayer.clearLayers();
  if (routeOptions) routeOptions.innerHTML = "";
  routeReady = false;
  showSafeSpaces = false;
  showCrowdAlerts = false;
  document.getElementById("safeSpacesButton")?.classList.remove("active");
  document.getElementById("crowdButton")?.classList.remove("active");
}

// ============================================================
// 8. WALKING ROUTE GENERATION
// ============================================================
// The public OSRM walking endpoint supplies route geometry. When
// fewer than three alternatives are returned, small midpoint detours
// are tried to obtain genuinely different walking options.
const OSRM_FOOT_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving";

function normaliseOsrmRoute(route) {
  return {
    coordinates: route.geometry.coordinates.map(([lng, lat]) => L.latLng(lat, lng)),
    summary: {
      totalDistance: route.distance,
      totalTime: route.duration
    }
  };
}

async function requestWalkingRoutes(points, alternatives = 0) {
  const coordinates = points
    .map(point => `${point.lng},${point.lat}`)
    .join(";");

  const params = new URLSearchParams({
    overview: "full",
    geometries: "geojson",
    steps: "false"
  });

  if (alternatives > 0) {
    params.set("alternatives", String(alternatives));
  }

  const response = await fetch(`${OSRM_FOOT_URL}/${coordinates}?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`Routing request failed (${response.status})`);
  }

  const data = await response.json();
  if (data.code !== "Ok" || !Array.isArray(data.routes)) {
    throw new Error(data.message || "Walking route could not be generated");
  }

  return data.routes.map(normaliseOsrmRoute);
}

function routesAreDifferent(first, second) {
  if (Math.abs(first.summary.totalDistance - second.summary.totalDistance) > 45) {
    return true;
  }
  const firstMid = first.coordinates[Math.floor(first.coordinates.length / 2)];
  const secondMid = second.coordinates[Math.floor(second.coordinates.length / 2)];
  return firstMid.distanceTo(secondMid) > 35;
}

function addUniqueRoute(routeList, candidate) {
  if (routeList.every(existing => routesAreDifferent(existing, candidate))) {
    routeList.push(candidate);
  }
}

async function getThreeWalkingRoutes(start, destination) {
  const candidates = [];
  const directRoutes = await requestWalkingRoutes([start, destination], 3);
  directRoutes.forEach(route => addUniqueRoute(candidates, route));

  if (candidates.length < 3) {
    const midLat = (start.lat + destination.lat) / 2;
    const midLng = (start.lng + destination.lng) / 2;
    const latDiff = destination.lat - start.lat;
    const lngDiff = destination.lng - start.lng;
    const length = Math.hypot(latDiff, lngDiff) || 1;
    const offsetSizes = [0.0011, -0.0011, 0.0018, -0.0018, 0.0024, -0.0024];

    for (const offset of offsetSizes) {
      if (candidates.length >= 3) break;
      const waypoint = L.latLng(
        midLat + (-lngDiff / length) * offset,
        midLng + (latDiff / length) * offset
      );
      try {
        const detourRoutes = await requestWalkingRoutes([start, waypoint, destination], 0);
        if (detourRoutes.length) addUniqueRoute(candidates, detourRoutes[0]);
      } catch (error) {
        console.warn("Alternative walking route skipped:", error);
      }
    }
  }

  return candidates
    .sort((a, b) => a.summary.totalDistance - b.summary.totalDistance)
    .slice(0, 3);
}

// Build route candidates, evaluate sensory conditions, place custom
// A/B markers, and initially select the recommended option.
async function buildRoute() {
  if (!startPoint || !destinationPoint) {
    showToast("Select both a starting point and a destination from the search results.");
    return;
  }

  resetRouteLayers();
  showToast("Finding three walking route options...");

  try {
    const routes = await getThreeWalkingRoutes(startPoint, destinationPoint);
    if (!routes.length) {
      showToast("A walking route could not be generated. Check your internet connection or locations.");
      return;
    }

    routeCandidates = routes.map(route => ({
      route,
      sensory: evaluateSensoryIndicator(route.coordinates)
    }));

    const startMarker = L.marker(startPoint, { icon: endpointIcon("start") }).bindPopup("Starting point");
    const endMarker = L.marker(destinationPoint, { icon: endpointIcon("end") }).bindPopup("Destination");
    startMarker.addTo(endpointLayer);
    endMarker.addTo(endpointLayer);

    const initialIndex = recommendedRouteIndex();
    selectRoute(initialIndex >= 0 ? initialIndex : 0, true);

    if (routeCandidates.length === 3) {
      showToast("Three walking route options found. Compare their sensory levels and choose a route.");
    } else {
      showToast(`${routeCandidates.length} distinct walking route option(s) could be generated for these locations.`);
    }
  } catch (error) {
    console.error(error);
    showToast("A walking route could not be generated. Check your internet connection or locations.");
  }
}

// ============================================================
// 9. USER CONTROLS AND THEME SWITCHING
// ============================================================
document.getElementById("routeButton").addEventListener("click", buildRoute);

document.getElementById("safeSpacesButton").addEventListener("click", event => {
  if (!routeReady) return;
  showSafeSpaces = !showSafeSpaces;
  if (showSafeSpaces) safeSpaceLayer.addTo(map);
  else if (map.hasLayer(safeSpaceLayer)) map.removeLayer(safeSpaceLayer);
  event.currentTarget.classList.toggle("active", showSafeSpaces);
});

document.getElementById("crowdButton").addEventListener("click", event => {
  if (!routeReady) return;
  showCrowdAlerts = !showCrowdAlerts;
  if (showCrowdAlerts) {
    crowdLayer.addTo(map);
    legend.classList.remove("hidden");
  } else {
    if (map.hasLayer(crowdLayer)) map.removeLayer(crowdLayer);
    legend.classList.add("hidden");
  }
  event.currentTarget.classList.toggle("active", showCrowdAlerts);
});

document.getElementById("clearButton").addEventListener("click", () => {
  startPoint = null;
  destinationPoint = null;
  startInput.value = "";
  destinationInput.value = "";
  resetRouteLayers();
  document.querySelector(".search-panel")?.classList.remove("hidden");
  map.fitBounds(CBD_BOUNDS);
});

// Back returns to the search card without deleting the generated route.
document.getElementById("backToSearch").addEventListener("click", () => {
  routeSummary.classList.add("hidden");
  document.body.classList.remove("route-view");
  document.querySelector(".search-panel")?.classList.remove("hidden");
});

// Switch both the UI variables and the map tile layer. The selected
// theme is stored locally so it persists across browser refreshes.
function setTheme(theme) {
  const isDark = theme === "dark";
  document.body.classList.toggle("dark", isDark);
  document.getElementById("themeToggle").textContent = isDark ? "☀" : "☾";
  localStorage.setItem("silento-theme", theme);

  if (isDark && map.hasLayer(lightTiles)) {
    map.removeLayer(lightTiles);
    darkTiles.addTo(map);
    darkTiles.bringToBack();
  } else if (!isDark && map.hasLayer(darkTiles)) {
    map.removeLayer(darkTiles);
    lightTiles.addTo(map);
    lightTiles.bringToBack();
  }
}

document.getElementById("themeToggle").addEventListener("click", () => {
  setTheme(document.body.classList.contains("dark") ? "light" : "dark");
});

setTheme(localStorage.getItem("silento-theme") || "light");
loadData();
