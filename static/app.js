const CBD_BOUNDS = L.latLngBounds([-37.826, 144.945], [-37.797, 144.979]);
const CBD_CENTER = [-37.8136, 144.9631];

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
let routingControl = null;
let startPoint = null;
let destinationPoint = null;
let currentRouteCoordinates = [];
let routeReady = false;

const startInput = document.getElementById("startInput");
const destinationInput = document.getElementById("destinationInput");
const startResultsBox = document.getElementById("startSearchResults");
const destinationResultsBox = document.getElementById("destinationSearchResults");
const routeSummary = document.getElementById("routeSummary");
const routeActions = document.getElementById("routeActions");
const legend = document.getElementById("legend");

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 2800);
}

function safeSpaceIcon() {
  return L.divIcon({
    className: "safe-space-icon-wrapper",
    html: '<div class="safe-space-marker" aria-hidden="true">⌂</div>',
    iconSize: [28, 28],
    iconAnchor: [14, 14]
  });
}

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
    // Acceptance behaviour: no safe-space or crowd overlay is shown before a route exists.
  } catch (error) {
    console.error(error);
    showToast("Could not load the original local datasets.");
  }
}

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

function sensorsNearRoute(routeCoordinates, thresholdMetres = 140) {
  return sensors.filter(sensor => {
    const point = L.latLng(Number(sensor.Latitude), Number(sensor.Longitude));
    return distanceToRoute(point, routeCoordinates) <= thresholdMetres;
  });
}

function safeSpacesNearRoute(routeCoordinates, thresholdMetres = 420) {
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
    .slice(0, 18);
}

function crowdStyle(status) {
  const styles = {
    low: { color: "#2f8f78", fillColor: "#45a98d", fillOpacity: 0.16 },
    medium: { color: "#c47b14", fillColor: "#e6a235", fillOpacity: 0.20 },
    high: { color: "#ad3447", fillColor: "#d65364", fillOpacity: 0.25 }
  };
  return styles[status] || styles.low;
}

function drawCrowdCoverage(routeCoordinates) {
  crowdLayer.clearLayers();
  const nearbySensors = sensorsNearRoute(routeCoordinates);

  nearbySensors.forEach(sensor => {
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
      fillOpacity: 0.06,
      opacity: 0.65,
      weight: 2,
      interactive: false
    });

    coverage.bindPopup(`
      <strong>${sensor.Sensor_Description || "Pedestrian sensor"}</strong><br>
      Current count: ${Math.round(sensor.current_count || 0)}<br>
      Next-hour estimate: ${Math.round(sensor.expected_count || 0)}<br>
      Predicted crowd level: ${sensor.status}
    `);
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
      ${place["Sub Theme"]}<br>
      About ${Math.round(place.routeDistance)} m from the route
    `);
    marker.addTo(safeSpaceLayer);
  });
  return nearbyPlaces;
}

function showRouteLayers(routeCoordinates) {
  currentRouteCoordinates = routeCoordinates;
  const nearbySensors = drawCrowdCoverage(routeCoordinates);
  drawSafeSpaces(routeCoordinates);

  crowdLayer.addTo(map);
  safeSpaceLayer.addTo(map);
  routeActions.classList.remove("hidden");
  legend.classList.remove("hidden");
  document.getElementById("safeSpacesButton").classList.add("active");
  document.getElementById("crowdButton").classList.add("active");
  return nearbySensors;
}

function updateRouteSummary(route, nearbySensors) {
  const highAlerts = nearbySensors.filter(sensor => sensor.status === "high");
  const mediumAlerts = nearbySensors.filter(sensor => sensor.status === "medium");

  document.getElementById("distanceValue").textContent = `${(route.summary.totalDistance / 1000).toFixed(1)} km`;
  document.getElementById("durationValue").textContent = `${Math.round(route.summary.totalTime / 60)} min`;
  document.getElementById("alertValue").textContent = highAlerts.length;

  const message = document.getElementById("alertMessage");
  if (highAlerts.length) {
    const names = highAlerts.slice(0, 3).map(item => item.Sensor_Description).join(", ");
    message.textContent = `High crowd coverage is predicted near ${names}. Nearby safe spaces are displayed along the route.`;
  } else if (mediumAlerts.length) {
    message.textContent = "No high-level warning was found, but some areas may have moderate pedestrian activity.";
  } else {
    message.textContent = "The available sensors indicate relatively low predicted crowd conditions along this route.";
  }
  routeSummary.classList.remove("hidden");
}

function resetRouteLayers() {
  crowdLayer.clearLayers();
  safeSpaceLayer.clearLayers();
  if (map.hasLayer(crowdLayer)) map.removeLayer(crowdLayer);
  if (map.hasLayer(safeSpaceLayer)) map.removeLayer(safeSpaceLayer);
  routeActions.classList.add("hidden");
  legend.classList.add("hidden");
  routeSummary.classList.add("hidden");
  currentRouteCoordinates = [];
  routeReady = false;
}

function buildRoute() {
  if (!startPoint || !destinationPoint) {
    showToast("Select both a starting point and a destination from the search results.");
    return;
  }

  resetRouteLayers();
  if (routingControl) map.removeControl(routingControl);

  routingControl = L.Routing.control({
    waypoints: [startPoint, destinationPoint],
    router: L.Routing.osrmv1({ serviceUrl: "https://routing.openstreetmap.de/routed-foot/route/v1" }),
    routeWhileDragging: false,
    addWaypoints: false,
    draggableWaypoints: false,
    fitSelectedRoutes: true,
    showAlternatives: false,
    lineOptions: { styles: [{ color: "#286854", opacity: 0.94, weight: 7 }] },
    createMarker(index, waypoint) {
      return L.marker(waypoint.latLng).bindPopup(index === 0 ? "Starting point" : "Destination");
    }
  })
    .on("routesfound", event => {
      const route = event.routes[0];
      routeReady = true;
      const nearbySensors = showRouteLayers(route.coordinates);
      updateRouteSummary(route, nearbySensors);
    })
    .on("routingerror", () => {
      resetRouteLayers();
      showToast("A walking route could not be generated. Check your internet connection or locations.");
    })
    .addTo(map);
}

document.getElementById("routeButton").addEventListener("click", buildRoute);

document.getElementById("safeSpacesButton").addEventListener("click", event => {
  if (!routeReady) return;
  if (map.hasLayer(safeSpaceLayer)) {
    map.removeLayer(safeSpaceLayer);
    event.currentTarget.classList.remove("active");
  } else {
    safeSpaceLayer.addTo(map);
    event.currentTarget.classList.add("active");
  }
});

document.getElementById("crowdButton").addEventListener("click", event => {
  if (!routeReady) return;
  if (map.hasLayer(crowdLayer)) {
    map.removeLayer(crowdLayer);
    legend.classList.add("hidden");
    event.currentTarget.classList.remove("active");
  } else {
    crowdLayer.addTo(map);
    legend.classList.remove("hidden");
    event.currentTarget.classList.add("active");
  }
});

document.getElementById("clearButton").addEventListener("click", () => {
  startPoint = null;
  destinationPoint = null;
  startInput.value = "";
  destinationInput.value = "";
  resetRouteLayers();
  if (routingControl) {
    map.removeControl(routingControl);
    routingControl = null;
  }
  map.fitBounds(CBD_BOUNDS);
});

document.getElementById("closeSummary").addEventListener("click", () => routeSummary.classList.add("hidden"));

function setTheme(theme) {
  const isDark = theme === "dark";
  document.body.classList.toggle("dark", isDark);
  document.getElementById("themeToggle").textContent = isDark ? "☀" : "☾";
  localStorage.setItem("calmroute-theme", theme);

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

setTheme(localStorage.getItem("calmroute-theme") || "light");
loadData();
