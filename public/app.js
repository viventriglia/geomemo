const LEVELS = {
  country: { label: "Country", focusZoom: 4 },
  region: { label: "Region/State", focusZoom: 6 },
  province: { label: "Province/County", focusZoom: 8 },
  city: { label: "City", focusZoom: 11 },
};

const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

const WORLD_LAT_LIMIT = 85.05112878;
const MAP_PAN_BOUNDS = L.latLngBounds([[-WORLD_LAT_LIMIT, -36000], [WORLD_LAT_LIMIT, 36000]]);
const DEFAULT_WORLD_CENTER = [20, 0];

function minimumWorldZoom() {
  return Math.max(2, Math.ceil(Math.log2(Math.max(window.innerWidth, window.innerHeight) / 256)));
}

const TILE_THEMES = {
  day: {
    url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
    options: {
      maxZoom: 19,
      subdomains: "abcd",
      attribution: TILE_ATTRIBUTION,
    },
  },
  night: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    options: {
      maxZoom: 19,
      subdomains: "abcd",
      attribution: TILE_ATTRIBUTION,
    },
  },
};

const map = L.map("map", {
  zoomControl: false,
  worldCopyJump: true,
  maxBounds: MAP_PAN_BOUNDS,
  maxBoundsViscosity: 1,
  minZoom: minimumWorldZoom(),
}).setView(DEFAULT_WORLD_CENTER, minimumWorldZoom());

const DEFAULT_WORLD_VIEW = { center: DEFAULT_WORLD_CENTER };

const selectedTitle = document.querySelector("#selectedTitle");
const selectedDetails = document.querySelector("#selectedDetails");
const notesInput = document.querySelector("#notes");
const saveButton = document.querySelector("#savePlace");
const clearButton = document.querySelector("#clearSelection");
const statusText = document.querySelector("#status");
const placesList = document.querySelector("#placesList");
const placeCount = document.querySelector("#placeCount");
const levelButtons = document.querySelectorAll("[data-level]");
const themeButtons = document.querySelectorAll("[data-theme]");
const zoomButtons = document.querySelectorAll("[data-zoom]");

const savedStyle = {
  color: "#1d4ed8",
  weight: 2,
  opacity: 0.9,
  fillColor: "#2563eb",
  fillOpacity: 0.36,
};

const selectedStyle = {
  color: "#0f3ea8",
  weight: 3,
  opacity: 1,
  fillColor: "#3b82f6",
  fillOpacity: 0.46,
};

const legacyPointStyle = {
  radius: 7,
  color: "#14746f",
  weight: 2,
  fillColor: "#25a18e",
  fillOpacity: 0.9,
  bubblingMouseEvents: false,
};

const visitedLayer = L.featureGroup().addTo(map);
let selectedLayer = null;
let selectedPlace = null;
let selectedLevel = "region";
let savedPlaces = [];
let layerById = new Map();
let activeTileLayer = null;

function preferredTheme() {
  const savedTheme = localStorage.getItem("geomemo-theme");
  if (savedTheme === "day" || savedTheme === "night") {
    return savedTheme;
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "night" : "day";
}

function setTheme(theme) {
  const nextTheme = theme === "night" ? "night" : "day";
  const tileTheme = TILE_THEMES[nextTheme];

  document.body.classList.toggle("theme-night", nextTheme === "night");
  localStorage.setItem("geomemo-theme", nextTheme);

  themeButtons.forEach((button) => {
    const isActive = button.dataset.theme === nextTheme;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });

  if (activeTileLayer) {
    activeTileLayer.remove();
  }
  activeTileLayer = L.tileLayer(tileTheme.url, tileTheme.options).addTo(map);
}

function setStatus(message, isError = false) {
  statusText.textContent = message;
  statusText.classList.toggle("status--error", isError);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatCoord(value) {
  return Number(value).toFixed(5);
}

function firstPresent(...values) {
  return values.find((value) => value && String(value).trim()) || "";
}

function geometryFor(place) {
  const geometry = place.geometry_geojson;
  if (!geometry) {
    return null;
  }
  if (typeof geometry === "string") {
    try {
      return JSON.parse(geometry);
    } catch {
      return null;
    }
  }
  return geometry;
}

function titleFor(place) {
  return firstPresent(
    place.area_name,
    place.city,
    place.province,
    place.county,
    place.state,
    place.region,
    place.country,
    place.display_name,
    `${formatCoord(place.lat)}, ${formatCoord(place.lng)}`,
  );
}

function subtitleFor(place) {
  const levelLabel = LEVELS[place.level]?.label || "";
  const parts = [
    levelLabel,
    firstPresent(place.city, place.locality),
    firstPresent(place.province, place.county),
    firstPresent(place.state, place.region),
    place.country,
  ].filter(Boolean);
  return [...new Set(parts)].join(" - ");
}

function renderDetails(place) {
  const rows = [
    ["Level", LEVELS[place.level]?.label],
    ["Country", place.country],
    ["Region/State", firstPresent(place.state, place.region)],
    ["Province/County", firstPresent(place.province, place.county)],
    ["City", firstPresent(place.city, place.locality)],
    ["Coordinates", `${formatCoord(place.lat)}, ${formatCoord(place.lng)}`],
  ].filter(([, value]) => value);

  selectedDetails.innerHTML = rows
    .map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
}

function popupHtml(place) {
  const notes = place.notes ? `<p>${escapeHtml(place.notes)}</p>` : "";
  return `
    <strong>${escapeHtml(titleFor(place))}</strong>
    <br>
    <span>${escapeHtml(subtitleFor(place))}</span>
    ${notes}
  `;
}

function focusLayer(layer, place, padding = [36, 36]) {
  if (layer?.getBounds) {
    const bounds = layer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds, {
        padding,
        maxZoom: LEVELS[place.level]?.focusZoom || 9,
      });
      return;
    }
  }
  map.flyTo([place.lat, place.lng], LEVELS[place.level]?.focusZoom || 8, { duration: 0.6 });
}

function savedAreasFitOptions(maxZoom = 10) {
  if (window.innerWidth <= 720) {
    return {
      paddingTopLeft: [40, 72],
      paddingBottomRight: [40, Math.round(window.innerHeight * 0.56)],
      maxZoom,
    };
  }

  return {
    paddingTopLeft: [72, 80],
    paddingBottomRight: [430, 48],
    maxZoom,
  };
}

function initialSavedAreasZoom() {
  if (savedPlaces.length === 0 || !visitedLayer.getBounds) {
    map.setView(DEFAULT_WORLD_VIEW.center, minimumWorldZoom());
    return;
  }

  const bounds = visitedLayer.getBounds();
  if (!bounds.isValid()) {
    return;
  }

  const maxZoom = Math.min(
    11,
    Math.max(...savedPlaces.map((place) => LEVELS[place.level]?.focusZoom || 8)),
  );

  map.fitBounds(bounds, savedAreasFitOptions(maxZoom));
}

function resetDefaultView() {
  initialSavedAreasZoom();
}

function applyWorldConstraints() {
  const nextMinZoom = minimumWorldZoom();
  map.setMinZoom(nextMinZoom);
  if (map.getZoom() < nextMinZoom) {
    map.setZoom(nextMinZoom, { animate: false });
  }
  map.panInsideBounds(MAP_PAN_BOUNDS, { animate: false });
}

function makeAreaLayer(place, style) {
  const geometry = geometryFor(place);
  if (geometry) {
    return L.geoJSON(geometry, { style, bubblingMouseEvents: false }).bindPopup(popupHtml(place));
  }
  return L.circleMarker([place.lat, place.lng], legacyPointStyle).bindPopup(popupHtml(place));
}

function setSelection(place) {
  clearSelection({ keepStatus: true });
  selectedPlace = place;
  selectedLayer = makeAreaLayer(place, selectedStyle).addTo(map);
  selectedTitle.textContent = titleFor(place);
  renderDetails(place);
  saveButton.disabled = false;
  clearButton.disabled = false;
  focusLayer(selectedLayer, place);
}

function clearSelection(options = {}) {
  selectedPlace = null;
  selectedTitle.textContent = "No selection";
  selectedDetails.innerHTML = "";
  notesInput.value = "";
  saveButton.disabled = true;
  clearButton.disabled = true;
  if (!options.keepStatus) {
    setStatus("");
  }
  if (selectedLayer) {
    selectedLayer.remove();
    selectedLayer = null;
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

async function selectArea(latlng) {
  clearSelection({ keepStatus: true });
  selectedTitle.textContent = "Looking up boundary...";
  clearButton.disabled = false;
  setStatus("");

  try {
    const area = await requestJson(`/api/area?lat=${latlng.lat}&lng=${latlng.lng}&level=${selectedLevel}`);
    setSelection(area);
  } catch (error) {
    selectedTitle.textContent = "No selection";
    setStatus(error.message, true);
  }
}

function renderSavedAreas() {
  visitedLayer.clearLayers();
  layerById = new Map();
  for (const place of savedPlaces) {
    const layer = makeAreaLayer(place, savedStyle).addTo(visitedLayer);
    layerById.set(place.id, layer);
  }
}

function renderList() {
  placeCount.textContent = String(savedPlaces.length);
  if (savedPlaces.length === 0) {
    placesList.innerHTML = '<p class="empty">No saved places.</p>';
    return;
  }

  placesList.innerHTML = savedPlaces
    .map((place) => {
      const created = place.created_at ? new Date(`${place.created_at}Z`).toLocaleDateString("en-US") : "";
      return `
        <article class="place">
          <button class="place__main" data-focus="${place.id}">
            <span class="place__title">${escapeHtml(titleFor(place))}</span>
            <span class="place__meta">${escapeHtml(subtitleFor(place))}</span>
            ${place.notes ? `<span class="place__notes">${escapeHtml(place.notes)}</span>` : ""}
          </button>
          <div class="place__footer">
            <span class="place__date">${escapeHtml(created)}</span>
            <button class="delete" data-delete="${place.id}">Delete</button>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadPlaces() {
  savedPlaces = await requestJson("/api/places");
  renderSavedAreas();
  renderList();
  initialSavedAreasZoom();
}

async function saveSelectedPlace() {
  if (!selectedPlace) {
    return;
  }

  saveButton.disabled = true;
  setStatus("Saving...");
  try {
    const payload = {
      ...selectedPlace,
      notes: notesInput.value,
    };
    const place = await requestJson("/api/places", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    savedPlaces = [place, ...savedPlaces];
    renderSavedAreas();
    renderList();
    clearSelection();
    setStatus("Saved.");
  } catch (error) {
    saveButton.disabled = false;
    setStatus(error.message, true);
  }
}

async function deletePlace(id) {
  await requestJson(`/api/places/${id}`, { method: "DELETE" });
  savedPlaces = savedPlaces.filter((place) => place.id !== id);
  renderSavedAreas();
  renderList();
}

function setLevel(level) {
  selectedLevel = level;
  levelButtons.forEach((button) => {
    const isActive = button.dataset.level === level;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  clearSelection();
}

map.on("click", (event) => {
  selectArea(event.latlng);
});

saveButton.addEventListener("click", saveSelectedPlace);
clearButton.addEventListener("click", () => clearSelection());

levelButtons.forEach((button) => {
  button.setAttribute("aria-pressed", String(button.classList.contains("is-active")));
  button.addEventListener("click", () => setLevel(button.dataset.level));
});

themeButtons.forEach((button) => {
  button.addEventListener("click", () => setTheme(button.dataset.theme));
});

zoomButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.zoom === "in") {
      map.zoomIn();
    } else if (button.dataset.zoom === "out") {
      map.zoomOut();
    } else {
      resetDefaultView();
    }
  });
});

placesList.addEventListener("click", async (event) => {
  const focusButton = event.target.closest("[data-focus]");
  const deleteButton = event.target.closest("[data-delete]");

  if (deleteButton) {
    const id = Number(deleteButton.dataset.delete);
    deleteButton.disabled = true;
    try {
      await deletePlace(id);
      setStatus("Deleted.");
    } catch (error) {
      deleteButton.disabled = false;
      setStatus(error.message, true);
    }
    return;
  }

  if (focusButton) {
    const id = Number(focusButton.dataset.focus);
    const place = savedPlaces.find((item) => item.id === id);
    const layer = layerById.get(id);
    if (place) {
      focusLayer(layer, place);
      setTimeout(() => layer?.openPopup?.(), 650);
    }
  }
});

loadPlaces().catch((error) => {
  setStatus(error.message, true);
});

setTheme(preferredTheme());
window.addEventListener("resize", applyWorldConstraints);
applyWorldConstraints();
