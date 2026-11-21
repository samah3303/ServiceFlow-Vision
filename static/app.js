const state = {
  project: null,
  results: null,
  view: "overview",
  drawTable: false,
  drawZoneType: null,
  tableDrag: null,
  selectedCameraId: null,
  pendingImagePoint: null,
  placingCamera: false,
  layoutImage: null,
  cameraImage: null,
  saveTimer: null,
  reviewTime: 0,
  reviewDuration: 0,
  reviewPlaying: false,
  reviewStartedAt: 0,
  reviewBaseTime: 0,
  reviewFrame: null,
  reviewIndex: new Map(),
  selectedReviewObservation: null,
  manualCameraId: null,
  manualBox: null,
  manualDrag: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function uid(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now()}${Math.random()}`.replace(".", "");
  return `${prefix}-${value.slice(0, 10)}`;
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(element._timer);
  element._timer = setTimeout(() => (element.className = "toast"), 3300);
}

function mediaUrl(path) {
  if (!path) return "";
  const normalized = String(path).replace(/\\/g, "/");
  return `/media/${normalized.startsWith("data/") ? normalized.slice(5) : normalized}`;
}

const viewTitles = {
  overview: "Build your vision workspace",
  layout: "Define the shared restaurant map",
  cameras: "Connect and calibrate camera views",
  people: "Enroll consented staff actors",
  analyze: "Analyze the restaurant timeline",
  review: "Validate identities and camera handoffs",
  reports: "Review observable service activity",
};

function showView(name) {
  state.view = name;
  $$(".view").forEach(element => element.classList.toggle("active", element.id === `view-${name}`));
  $$(".nav-item").forEach(element => element.classList.toggle("active", element.dataset.view === name));
  $("#viewTitle").textContent = viewTitles[name];
  if (name === "layout") drawLayoutEditor();
  if (name === "cameras") drawCalibration();
  if (name === "review") renderReview();
  if (name === "reports") drawReportMap();
  if (name !== "review" && state.reviewPlaying) pauseReview();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function saveProject(immediate = false) {
  clearTimeout(state.saveTimer);
  const execute = async () => {
    $("#saveState").textContent = "Saving…";
    try {
      state.project = await api("/api/project", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state.project),
      });
      $("#saveState").textContent = "Saved locally";
      renderOverview();
      renderReadiness();
    } catch (error) {
      $("#saveState").textContent = "Could not save";
      toast(error.message, true);
    }
  };
  if (immediate) return execute();
  state.saveTimer = setTimeout(execute, 350);
}

function loadLayoutImage() {
  const source = state.project?.restaurant?.layoutImage;
  if (!source) {
    state.layoutImage = null;
    return;
  }
  const image = new Image();
  image.onload = () => {
    state.layoutImage = image;
    drawLayoutEditor();
    drawCalibrationMap();
    drawReportMap();
  };
  image.src = source;
}

function paintMap(canvas, { trajectories = null, calibration = null } = {}) {
  if (!canvas || !state.project) return;
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f2f4ef";
  ctx.fillRect(0, 0, width, height);
  if (state.layoutImage) {
    ctx.save();
    ctx.globalAlpha = 0.9;
    ctx.drawImage(state.layoutImage, 0, 0, width, height);
    ctx.restore();
  } else {
    ctx.strokeStyle = "#e2e6df";
    ctx.lineWidth = 1;
    for (let x = 0; x <= width; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke(); }
    for (let y = 0; y <= height; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  }

  const zoneStyle = {
    seat: ["rgba(78,166,111,.16)", "#3f9360"],
    handoff: ["rgba(124,92,230,.14)", "#7755d8"],
    entrance: ["rgba(255,122,94,.16)", "#dc6049"],
    waiting: ["rgba(224,166,61,.16)", "#c58b25"],
    staff: ["rgba(62,132,173,.14)", "#397b9f"],
  };
  (state.project.zones || []).forEach(zone => {
    const [fill, stroke] = zoneStyle[zone.type] || zoneStyle.handoff;
    const x = zone.x * width, y = zone.y * height, w = zone.w * width, h = zone.h * height;
    ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = 1.5; ctx.setLineDash([7, 5]);
    ctx.fillRect(x, y, w, h); ctx.strokeRect(x, y, w, h); ctx.setLineDash([]);
    ctx.fillStyle = stroke; ctx.font = "700 9px system-ui"; ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText(zone.name || zone.type, x + 6, y + 6);
  });

  (state.project.tables || []).forEach((table, index) => {
    const x = table.x * width, y = table.y * height, w = table.w * width, h = table.h * height;
    ctx.fillStyle = "rgba(186,242,205,.78)";
    ctx.strokeStyle = "#183d2f";
    ctx.lineWidth = 2;
    roundedRect(ctx, x, y, w, h, Math.min(13, w / 5, h / 5));
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#183d2f";
    ctx.font = "700 13px system-ui";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(table.number || index + 1), x + w / 2, y + h / 2);
  });

  (state.project.cameras || []).forEach(camera => {
    if (!camera.mapPosition) return;
    const x = camera.mapPosition[0] * width, y = camera.mapPosition[1] * height;
    ctx.fillStyle = camera.id === state.selectedCameraId ? "#ff745e" : "#183d2f";
    ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#182018"; ctx.font = "650 10px system-ui"; ctx.textAlign = "left";
    ctx.fillText(camera.name, x + 12, y + 3);
  });

  if (trajectories) {
    Object.entries(trajectories).forEach(([identity, points], index) => {
      const valid = points.filter(item => item.map && Number.isFinite(item.map[0]) && Number.isFinite(item.map[1]));
      if (valid.length < 2) return;
      ctx.strokeStyle = pathColor(identity, index);
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = identity.startsWith("staff:") ? 3 : 1.5;
      ctx.beginPath();
      valid.forEach((item, pointIndex) => {
        const x = item.map[0] * width, y = item.map[1] * height;
        if (pointIndex === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.globalAlpha = 1;
    });
  }

  if (calibration) {
    calibration.forEach((pair, index) => marker(ctx, pair.map[0] * width, pair.map[1] * height, index + 1, "#ff745e"));
  }

  if (canvas.id === "layoutCanvas" && state.tableDrag) {
    const drag = state.tableDrag;
    ctx.setLineDash([8, 5]); ctx.strokeStyle = "#ff745e"; ctx.lineWidth = 2;
    ctx.strokeRect(Math.min(drag.start.x, drag.end.x) * width, Math.min(drag.start.y, drag.end.y) * height, Math.abs(drag.end.x - drag.start.x) * width, Math.abs(drag.end.y - drag.start.y) * height);
    ctx.setLineDash([]);
  }
}

function roundedRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, width, height, radius) : ctx.rect(x, y, width, height);
}

function marker(ctx, x, y, number, color) {
  ctx.fillStyle = color; ctx.strokeStyle = "white"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(x, y, 11, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "white"; ctx.font = "800 10px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(number), x, y + .5);
}

function pathColor(identity, index) {
  if (identity.startsWith("staff:")) return ["#ff745e", "#7c5ce6", "#d39d25", "#1f8b8c"][index % 4];
  return ["#3e9260", "#4e84ad", "#9a6ca8", "#aa7952"][index % 4];
}

function eventPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}

function renderOverview() {
  const project = state.project;
  if (!project) return;
  const tables = project.tables.length;
  const cameras = project.cameras.length;
  const calibrated = project.cameras.filter(camera => camera.calibration.length >= 4).length;
  const staff = project.staff.length;
  $("#overviewTables").textContent = `${tables} table${tables === 1 ? "" : "s"}`;
  $("#overviewLayout").textContent = project.restaurant.layoutImage ? "Floor plan uploaded" : "Blank canvas ready";
  $("#overviewCameras").textContent = `${cameras} connected`;
  $("#overviewCalibrated").textContent = `${calibrated} calibrated · ${(project.cameraLinks || []).length} routes`;
  $("#overviewStaff").textContent = `${staff} enrolled`;
  const routesReady = cameras < 2 || (project.cameraLinks || []).length > 0;
  const ready = tables > 0 && cameras > 0 && calibrated === cameras && routesReady && staff > 0;
  $("#overviewReadiness").textContent = ready ? "Ready to analyze" : "Set up needed";
  $("#overviewNext").textContent = !tables ? "Mark your tables" : !cameras ? "Add camera footage" : calibrated < cameras ? "Calibrate every camera" : !routesReady ? "Connect the camera routes" : !staff ? "Enroll staff actors" : "All required inputs are present";
}

function drawLayoutEditor() { paintMap($("#layoutCanvas")); }

function renderTables() {
  const list = $("#tableList");
  const tables = state.project.tables;
  $("#tableCountBadge").textContent = tables.length;
  if (!tables.length) {
    list.className = "stack-list empty-list";
    list.textContent = "No tables yet";
    return;
  }
  list.className = "stack-list";
  list.innerHTML = tables.map(table => `
    <div class="table-item" data-id="${escapeHTML(table.id)}">
      <div class="table-swatch"></div>
      <input aria-label="Table number" value="${escapeHTML(table.number)}" />
      <button class="icon-button" aria-label="Remove table">×</button>
    </div>`).join("");
  $$(".table-item", list).forEach(row => {
    const table = tables.find(item => item.id === row.dataset.id);
    $("input", row).addEventListener("change", event => { table.number = event.target.value.trim() || table.number; saveProject(); drawLayoutEditor(); });
    $("button", row).addEventListener("click", async () => {
      state.project = await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "table", id: table.id }) });
      renderAll();
    });
  });
}

function renderZones() {
  const list = $("#zoneList");
  const zones = state.project.zones || [];
  $("#zoneCountBadge").textContent = zones.length;
  if (!zones.length) { list.className = "stack-list empty-list"; list.textContent = "No zones yet"; return; }
  const labels = { seat: "Seat zone", handoff: "Camera handoff", entrance: "Entrance / exit", waiting: "Waiting area", staff: "Staff-only area" };
  list.className = "stack-list";
  list.innerHTML = zones.map(zone => {
    const table = state.project.tables.find(item => item.id === zone.tableId);
    return `<div class="zone-item" data-id="${zone.id}"><span class="zone-swatch"></span><div class="zone-item-copy"><strong>${escapeHTML(zone.name)}</strong><span>${labels[zone.type] || zone.type}${table ? ` · Table ${escapeHTML(table.number)}` : ""}</span></div><button class="icon-button" aria-label="Remove zone">×</button></div>`;
  }).join("");
  $$(".zone-item", list).forEach(row => $("button", row).addEventListener("click", async () => {
    state.project = await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "zone", id: row.dataset.id }) });
    renderAll();
  }));
}

function renderCameras() {
  const cameras = state.project.cameras;
  const list = $("#cameraList");
  $("#cameraCountBadge").textContent = cameras.length;
  if (!state.selectedCameraId && cameras.length) state.selectedCameraId = cameras[0].id;
  if (!cameras.some(camera => camera.id === state.selectedCameraId)) state.selectedCameraId = cameras[0]?.id || null;
  if (!cameras.length) {
    list.className = "camera-list empty-list";
    list.textContent = "No camera recordings yet";
  } else {
    list.className = "camera-list";
    list.innerHTML = cameras.map(camera => {
      const points = camera.calibration?.length || 0;
      const quality = camera.calibrationQuality;
      const meta = camera.metadata || {};
      return `<article class="camera-card" data-id="${camera.id}">
        <div class="camera-preview"><img src="/api/frame?cameraId=${camera.id}" alt="${escapeHTML(camera.name)} preview" /><span class="camera-badge">${points >= 4 ? `${quality?.label || "CALIBRATED"} · ${quality?.score ?? "—"}` : `${points}/4 POINTS`}</span></div>
        <div class="camera-info"><strong>${escapeHTML(camera.name)}</strong><div class="camera-meta">${meta.width || "?"}×${meta.height || "?"} · ${formatTime(meta.durationSeconds || 0)} · offset ${camera.timeOffsetSeconds || 0}s</div>
        <div class="camera-actions"><button class="button secondary calibrate">${points >= 4 ? "Review calibration" : "Calibrate"}</button><button class="icon-button remove" aria-label="Remove camera">×</button></div></div>
      </article>`;
    }).join("");
    $$(".camera-card", list).forEach(card => {
      $(".calibrate", card).addEventListener("click", () => { state.selectedCameraId = card.dataset.id; state.pendingImagePoint = null; state.placingCamera = false; renderCalibrationSelector(); showView("cameras"); setTimeout(() => $("#calibrationPanel").scrollIntoView({ behavior: "smooth" }), 50); });
      $(".remove", card).addEventListener("click", async () => {
        if (!confirm("Remove this camera recording from the local project?")) return;
        state.project = await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "camera", id: card.dataset.id }) });
        renderAll();
      });
    });
  }
  renderCalibrationSelector();
  renderManualMarks();
  renderCameraClocks();
  renderCameraLinks();
}

function renderCameraClocks() {
  const cameras = state.project.cameras || [];
  $("#syncTimestampsButton").disabled = cameras.length < 2;
  $("#timestampSyncList").innerHTML = cameras.length ? cameras.map(camera => {
    const sync = camera.timestampSync || {};
    const status = sync.status === "verified" ? `Verified from ${sync.samples} readings · ${escapeHTML(sync.startTime || "")}` : sync.reason || "Not checked yet";
    return `<div class="clock-item"><strong>${escapeHTML(camera.name)}</strong><span>${escapeHTML(status)} · effective offset ${Number(camera.timeOffsetSeconds || 0).toFixed(2)}s</span><label>Manual fine shift, seconds<input type="number" min="-30" max="30" step="0.25" value="${Number(camera.manualTimeAdjustmentSeconds || 0)}" data-clock-adjust-camera="${escapeHTML(camera.id)}" /></label></div>`;
  }).join("") : `<div class="audit-empty">Add recordings to compare their clocks.</div>`;
}

function renderCameraLinks() {
  const cameras = state.project.cameras || [];
  const routes = state.project.cameraLinks ||= [];
  const cameraOptions = cameras.map(camera => `<option value="${camera.id}">${escapeHTML(camera.name)}</option>`).join("");
  $("#routeFromCamera").innerHTML = cameraOptions || `<option value="">Add cameras first</option>`;
  $("#routeToCamera").innerHTML = cameraOptions || `<option value="">Add cameras first</option>`;
  if (cameras.length > 1 && $("#routeFromCamera").value === $("#routeToCamera").value) $("#routeToCamera").selectedIndex = 1;
  const eligibleZones = (state.project.zones || []).filter(zone => ["handoff", "entrance", "waiting"].includes(zone.type));
  const zoneOptions = `<option value="">No required zone</option>` + eligibleZones.map(zone => `<option value="${zone.id}">${escapeHTML(zone.name)}</option>`).join("");
  $("#routeExitZone").innerHTML = zoneOptions;
  $("#routeEntryZone").innerHTML = zoneOptions;
  $("#cameraLinkCountBadge").textContent = routes.length;
  const list = $("#cameraLinkList");
  if (!routes.length) {
    list.className = "route-list empty-list";
    list.textContent = cameras.length < 2 ? "Add at least two cameras to configure movement." : "No camera routes yet.";
    return;
  }
  const cameraName = id => cameras.find(camera => camera.id === id)?.name || "Removed camera";
  const zoneName = id => eligibleZones.find(zone => zone.id === id)?.name;
  list.className = "route-list";
  list.innerHTML = routes.map(route => {
    const direction = route.bidirectional ? "↔" : "→";
    const zones = [zoneName(route.exitZoneId), zoneName(route.entryZoneId)].filter(Boolean).join(" → ");
    const detail = route.mode === "overlap" ? `Overlapping views · max ${Number(route.maxDistance).toFixed(2)} map distance` : `${Number(route.minSeconds)}–${Number(route.maxSeconds)}s travel · max ${Number(route.maxDistance).toFixed(2)} map distance`;
    return `<div class="route-item" data-id="${route.id}"><i></i><div class="route-copy"><strong>${escapeHTML(cameraName(route.fromCameraId))} ${direction} ${escapeHTML(cameraName(route.toCameraId))}</strong><span>${escapeHTML(detail)}${zones ? ` · ${escapeHTML(zones)}` : ""}</span></div><button class="icon-button" aria-label="Remove camera route">×</button></div>`;
  }).join("");
  $$(".route-item", list).forEach(row => $("button", row).addEventListener("click", async () => {
    state.project.cameraLinks = routes.filter(route => route.id !== row.dataset.id);
    await saveProject(true);
    renderCameraLinks();
  }));
}

function renderCalibrationSelector() {
  const select = $("#calibrationCameraSelect");
  const cameras = state.project.cameras;
  select.innerHTML = cameras.length ? cameras.map(camera => `<option value="${camera.id}">${escapeHTML(camera.name)}</option>`).join("") : `<option value="">Add a camera first</option>`;
  select.value = state.selectedCameraId || "";
  select.disabled = !cameras.length;
  drawCalibration();
}

function selectedCamera() { return state.project.cameras.find(camera => camera.id === state.selectedCameraId); }

function drawCalibration() {
  drawCameraCanvas();
  drawCalibrationMap();
  renderCalibrationPoints();
}

function drawCameraCanvas() {
  const canvas = $("#cameraCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#202722"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const camera = selectedCamera();
  if (!camera) { ctx.fillStyle = "#94a097"; ctx.font = "14px system-ui"; ctx.textAlign = "center"; ctx.fillText("Add and select a camera", canvas.width / 2, canvas.height / 2); return; }
  const image = new Image();
  image.onload = () => {
    state.cameraImage = image;
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    (camera.calibration || []).forEach((pair, index) => marker(ctx, pair.image[0] * canvas.width, pair.image[1] * canvas.height, index + 1, "#ff745e"));
    if (state.pendingImagePoint) marker(ctx, state.pendingImagePoint.x * canvas.width, state.pendingImagePoint.y * canvas.height, (camera.calibration || []).length + 1, "#7755d8");
  };
  image.src = `/api/frame?cameraId=${camera.id}&v=${camera.calibration?.length || 0}`;
}

function drawCalibrationMap() {
  const camera = selectedCamera();
  paintMap($("#calibrationMapCanvas"), { calibration: camera?.calibration || [] });
}

function renderCalibrationPoints() {
  const camera = selectedCamera();
  const points = camera?.calibration || [];
  $("#calibrationPoints").innerHTML = points.length ? points.map((_, index) => `<span class="point-pill ${points.length >= 4 ? "good" : ""}">Point ${index + 1}</span>`).join("") : `<span class="point-pill">0 of 4 minimum</span>`;
  if (!camera) $("#calibrationInstruction").textContent = "Add a camera recording to begin.";
  else if (state.placingCamera) $("#calibrationInstruction").textContent = "Click the camera’s approximate location on the floor plan.";
  else if (state.pendingImagePoint) $("#calibrationInstruction").textContent = "Now click the same physical point on the floor plan.";
  else if (points.length < 4) $("#calibrationInstruction").textContent = `Click calibration point ${points.length + 1} in the camera image.`;
  else $("#calibrationInstruction").textContent = `${points.length} points matched · ${camera.calibrationQuality?.label || "ready"} calibration (${camera.calibrationQuality?.score ?? "—"}/100). Add spread-out points to improve it.`;
}

function manualCamera() { return state.project.cameras.find(camera => camera.id === state.manualCameraId); }

function renderManualMarks() {
  const cameras = state.project.cameras || [];
  if (!cameras.some(camera => camera.id === state.manualCameraId)) state.manualCameraId = state.selectedCameraId || cameras[0]?.id || null;
  const select = $("#manualCameraSelect");
  select.innerHTML = cameras.map(camera => `<option value="${camera.id}">${escapeHTML(camera.name)}</option>`).join("") || `<option value="">Add a camera first</option>`;
  select.value = state.manualCameraId || "";
  select.disabled = !cameras.length;
  const camera = manualCamera();
  const video = $("#manualVideo");
  const canvas = $("#manualCanvas");
  canvas.width = Number(camera?.metadata?.width || 800);
  canvas.height = Number(camera?.metadata?.height || 450);
  $("#manualVideoWrap").style.aspectRatio = `${canvas.width} / ${canvas.height}`;
  const source = camera?.videoPath ? mediaUrl(camera.videoPath) : "";
  if (video.getAttribute("src") !== source) {
    video.pause();
    if (source) video.setAttribute("src", source); else video.removeAttribute("src");
    video.load();
    state.manualBox = null;
  }
  const duration = Number(camera?.metadata?.durationSeconds || 0);
  $("#manualTimeline").max = duration || 1;
  $("#manualDuration").textContent = formatClock(duration);
  if (!camera) $("#manualDrawStatus").textContent = "Add a camera recording first.";
  $("#manualStaff").innerHTML = state.project.staff.map(person => `<option value="${person.id}">${escapeHTML(person.name)}</option>`).join("") || `<option value="">Enroll staff first</option>`;
  $("#manualTable").innerHTML = `<option value="">No table / use floor position</option>` + state.project.tables.map(table => `<option value="${table.id}">Table ${escapeHTML(table.number)}</option>`).join("");
  const marks = (state.project.manualMarks || []).filter(mark => mark.cameraId === state.manualCameraId);
  $("#manualCountBadge").textContent = marks.length;
  const list = $("#manualMarkList");
  list.className = `manual-mark-list${marks.length ? "" : " empty-list"}`;
  list.innerHTML = marks.length ? marks.sort((a, b) => a.startSeconds - b.startSeconds).map(mark => {
    const staff = state.project.staff.find(person => person.id === mark.staffId);
    const table = state.project.tables.find(item => item.id === mark.tableId);
    const label = mark.role === "staff" ? staff?.name || "Staff" : "Customer";
    return `<div class="manual-mark-item" data-id="${escapeHTML(mark.id)}"><div><strong>${escapeHTML(label)}</strong><span>${formatClock(mark.startSeconds)}–${formatClock(mark.endSeconds)}${table ? ` · Table ${escapeHTML(table.number)}` : ""}</span></div><button class="icon-button" type="button" aria-label="Remove marking">×</button></div>`;
  }).join("") : "No people marked for this camera.";
  $$(".manual-mark-item button", list).forEach(button => button.addEventListener("click", async () => {
    const id = button.closest(".manual-mark-item").dataset.id;
    state.project.manualMarks = state.project.manualMarks.filter(mark => mark.id !== id);
    await saveProject(true); renderManualMarks(); drawManualCanvas();
  }));
  drawManualCanvas();
}

function drawManualCanvas() {
  const canvas = $("#manualCanvas"), ctx = canvas.getContext("2d"), video = $("#manualVideo");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const current = video.currentTime || 0;
  const marks = (state.project?.manualMarks || []).filter(mark => mark.cameraId === state.manualCameraId && current >= mark.startSeconds && current <= mark.endSeconds);
  for (const mark of marks) {
    const [x, y, w, h] = mark.bbox;
    ctx.strokeStyle = mark.role === "staff" ? "#ff745e" : "#55d48b";
    ctx.lineWidth = Math.max(2, canvas.width / 300); ctx.setLineDash([]);
    ctx.strokeRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height);
  }
  const box = state.manualDrag ? [Math.min(state.manualDrag.start.x, state.manualDrag.end.x), Math.min(state.manualDrag.start.y, state.manualDrag.end.y), Math.abs(state.manualDrag.end.x - state.manualDrag.start.x), Math.abs(state.manualDrag.end.y - state.manualDrag.start.y)] : state.manualBox;
  if (box) {
    ctx.strokeStyle = "#f9d978"; ctx.lineWidth = Math.max(2, canvas.width / 250); ctx.setLineDash([8, 5]);
    ctx.strokeRect(box[0] * canvas.width, box[1] * canvas.height, box[2] * canvas.width, box[3] * canvas.height);
    ctx.setLineDash([]);
  }
}

function renderStaff() {
  const list = $("#staffList");
  const staff = state.project.staff;
  $("#staffCountBadge").textContent = staff.length;
  if (!staff.length) { list.className = "people-list empty-list"; list.textContent = "No staff profiles yet"; return; }
  list.className = "people-list";
  list.innerHTML = staff.map(person => `<div class="person-item" data-id="${person.id}"><div class="person-avatar">${person.photoPath ? `<img src="${mediaUrl(person.photoPath)}" alt="${escapeHTML(person.name)}" />` : escapeHTML(person.name.slice(0, 1).toUpperCase())}</div><div class="person-copy"><strong>${escapeHTML(person.name)}</strong><small>${escapeHTML((person.role || "waiter").replace(/^./, value => value.toUpperCase()))} · ${person.photoPath ? "face profile ready" : "video-only; identify in Review"}</small></div><button class="icon-button" aria-label="Remove staff profile">×</button></div>`).join("");
  $$(".person-item", list).forEach(row => $("button", row).addEventListener("click", async () => {
    if (!confirm("Remove this staff profile?")) return;
    state.project = await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "staff", id: row.dataset.id }) });
    renderAll();
  }));
}

function renderReadiness() {
  if (!state.project) return;
  const cameras = state.project.cameras;
  const checks = [
    [state.project.tables.length > 0, `${state.project.tables.length} table${state.project.tables.length === 1 ? "" : "s"} mapped`],
    [cameras.length > 0 && cameras.every(camera => camera.calibration.length >= 4), cameras.length ? `${cameras.filter(camera => camera.calibration.length >= 4).length}/${cameras.length} cameras calibrated` : "Camera footage needed"],
    [cameras.length < 2 || (state.project.cameraLinks || []).length > 0, cameras.length < 2 ? "Single-camera run" : `${(state.project.cameraLinks || []).length} camera route${(state.project.cameraLinks || []).length === 1 ? "" : "s"}`],
    [state.project.staff.length > 0, `${state.project.staff.length} staff profile${state.project.staff.length === 1 ? "" : "s"}`],
  ];
  $("#readinessList").innerHTML = checks.map(([ok, label]) => `<div class="ready-item ${ok ? "ok" : ""}">${escapeHTML(label)}</div>`).join("");
  $("#analyzeButton").disabled = !checks.every(check => check[0]);
  const settings = state.project.settings;
  $("#sampleFps").value = settings.sampleFps;
  $("#tableRadius").value = settings.tableRadius;
  $("#handoffDistance").value = settings.handoffDistance ?? 0.12;
  $("#handoffSeconds").value = settings.handoffSeconds ?? 8;
  $("#bodyReidThreshold").value = settings.bodyReidThreshold ?? 0.58;
  $("#staffThreshold").value = settings.staffFaceThreshold;
  $("#customerThreshold").value = settings.customerFaceThreshold;
}

function formatTime(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "—";
  seconds = Math.max(0, Number(seconds));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60), secs = Math.round(seconds % 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function renderReports() {
  const results = state.results;
  $("#reportEmpty").hidden = !!results;
  $("#reportContent").hidden = !results;
  $("#exportButton").disabled = !results;
  if (!results) return;
  const oldOffsets = new Map((results.timestampSync?.cameras || []).map(row => [row.cameraId, row.offsetSeconds]));
  const offsetsChanged = [...oldOffsets].some(([id, offset]) => offset != null && Math.abs(Number(state.project.cameras.find(camera => camera.id === id)?.timeOffsetSeconds || 0) - Number(offset)) > .1);
  const syncNewerThanReport = resultTimingIsStale();
  const reviewCount = Number(results.summary?.observationsNeedingIdentityReview || 0);
  $("#reportGenerated").textContent = `Generated locally ${new Date(results.generatedAt).toLocaleString()}${offsetsChanged || syncNewerThanReport ? " · Camera offsets changed; rerun analysis" : ""}${reviewCount ? ` · ${reviewCount.toLocaleString()} observations need identity review` : ""}`;
  const summary = results.summary;
  const metrics = [
    ["Anonymous customers", summary.customers, "Temporary identities"],
    ["Staff detected", summary.staffDetected, "Enrolled profiles matched"],
    ["Table sessions", summary.tableSessions, "Observed occupied periods"],
    ["Camera views", summary.camerasAnalyzed, `${summary.observations.toLocaleString()} observations${summary.manualObservations ? ` · ${summary.manualObservations.toLocaleString()} human-assisted` : ""}${summary.estimatedSeatedObservations ? ` · ${summary.estimatedSeatedObservations.toLocaleString()} estimated seated` : ""}`],
  ];
  $("#reportMetrics").innerHTML = metrics.map(([label, value, note], index) => `<article class="metric-card ${index === 3 ? "accent-card" : ""}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  const calibration = results.autoCalibration?.cameras || [];
  const calibrationMessages = {
    reference: "Reference camera",
    corrected: "Small map shift applied for this analysis",
    aligned: "Map positions already agree",
    "insufficient-evidence": "Not enough independent staff matches to adjust safely",
    "conflicting-evidence": "Matches disagree; check calibration points",
    "large-error-needs-review": "Possible large error; manual calibration review needed",
  };
  const clockRefinement = results.spatialSync?.cameras || [];
  const clockMessages = { reference: "Reference clock", aligned: "Shared movement agrees with the clock",
    corrected: "Shared movement refined playback by", "insufficient-movement": "Not enough moving staff to verify clock",
    "insufficient-overlap": "Not enough shared movement to verify clock",
    "ambiguous-needs-review": "Movement timing is ambiguous; clock left unchanged" };
  $("#reportCalibration").innerHTML = calibration.length ? calibration.map(row => {
    const name = state.project.cameras.find(camera => camera.id === row.cameraId)?.name || row.cameraId;
    const shift = row.status === "corrected" ? ` (${row.shift.map(value => Number(value).toFixed(3)).join(", ")})` : "";
    const clock = clockRefinement.find(item => item.cameraId === row.cameraId);
    const clockText = clock ? `${clockMessages[clock.status] || clock.status}${clock.status === "corrected" ? ` ${Number(clock.adjustmentSeconds).toFixed(1)}s` : ""}` : "Clock refinement unavailable";
    return `<div class="calibration-status-row"><strong>${escapeHTML(name)}</strong><span>${escapeHTML(clockText)} · ${escapeHTML(calibrationMessages[row.status] || row.status)}${shift}</span></div>`;
  }).join("") : `<p>Rerun analysis to check the corrected camera clocks and floor-plan alignment.</p>`;
  $("#tableReport").innerHTML = results.tables.length ? results.tables.map(table => `<tr><td>${escapeHTML(table.tableNumber)}</td><td>${table.turns}</td><td>${table.occupiedMinutes} min</td><td>${table.averagePartySize}</td><td>${table.averageFirstServiceSeconds == null ? "Not observed" : `${table.averageFirstServiceSeconds}s`}</td><td>${table.staffVisits}</td></tr>`).join("") : `<tr><td colspan="6">No table sessions were detected.</td></tr>`;
  const maxVisits = Math.max(1, ...results.staff.map(person => person.tableVisits));
  $("#staffReport").innerHTML = results.staff.length ? results.staff.map(person => `<div class="staff-report-row"><strong>${escapeHTML(person.name)}</strong><span>${person.tableVisits} visits · ${person.interactionMinutes} min near tables · ${person.firstContacts} first contacts</span><div class="staff-bar"><div style="width:${(person.tableVisits / maxVisits) * 100}%"></div></div></div>`).join("") : `<div class="staff-report-row"><span>No enrolled staff were matched in this run.</span></div>`;
  $("#methodNotes").innerHTML = results.methodNotes.map(note => `<span>${escapeHTML(note)}</span>`).join("");
  drawReportMap();
}

function identityLabel(identity) {
  if (!identity) return "Unknown";
  if (identity.startsWith("staff:")) {
    const id = identity.split(":", 2)[1];
    return state.project.staff.find(person => person.id === id)?.name || `Staff ${id}`;
  }
  if (identity.startsWith("customer:manual:")) return `Marked customer ${identity.slice(-4)}`;
  if (identity.startsWith("customer:collision:")) return `Review customer ${identity.split(":").slice(-2).join("-")}`;
  if (identity.startsWith("unresolved:")) return `Review identity ${identity.split(":").slice(-2).join("-")}`;
  return identity.replace("customer:", "Customer ").replace("pending:", "Pending ");
}

function buildReviewIndex() {
  state.reviewIndex = new Map();
  const fps = Number(state.project.settings.sampleFps || 3);
  (state.results?.observations || []).forEach(observation => {
    const key = `${observation.cameraId}:${Math.round(observation.time * fps)}`;
    if (!state.reviewIndex.has(key)) state.reviewIndex.set(key, []);
    state.reviewIndex.get(key).push(observation);
  });
}

function observationsAt(cameraId, time) {
  const fps = Number(state.project.settings.sampleFps || 3);
  const bucket = Math.round(time * fps);
  const nearby = [bucket - 1, bucket, bucket + 1]
    .flatMap(value => state.reviewIndex.get(`${cameraId}:${value}`) || [])
    .filter(observation => Math.abs(observation.time - time) <= Math.max(.22, .7 / fps));
  if (!nearby.length) return [];
  const nearestTime = nearby.reduce((best, item) => Math.abs(item.time - time) < Math.abs(best - time) ? item.time : best, nearby[0].time);
  // Show one sampled frame only. A tolerance can combine boxes from adjacent
  // timestamps and falsely make one identity appear twice on the canvas.
  return nearby.filter(observation => observation.time === nearestTime);
}

function renderReview() {
  const available = !!state.results;
  $("#reviewEmpty").hidden = available;
  $("#reviewContent").hidden = !available;
  $("#exportTrainingButton").disabled = !available;
  if (!available) return;
  $("#reviewTimingWarning").hidden = !resultTimingIsStale();
  buildReviewIndex();
  const cameras = state.project.cameras.filter(camera => camera.videoPath);
  state.reviewDuration = Math.max(0, ...cameras.map(camera => effectiveCameraOffset(camera) + Number(camera.metadata?.durationSeconds || 0)));
  state.reviewTime = Math.min(state.reviewTime, state.reviewDuration);
  $("#reviewTimeline").max = state.reviewDuration || 1;
  $("#reviewTimeline").value = state.reviewTime;
  $("#reviewDuration").textContent = formatClock(state.reviewDuration);

  const grid = $("#reviewVideoGrid");
  const signature = cameras.map(camera => camera.id).join(",");
  if (grid.dataset.signature !== signature) {
    grid.dataset.signature = signature;
    grid.innerHTML = cameras.map(camera => `<div class="review-feed" data-camera-id="${camera.id}"><span class="feed-label">${escapeHTML(camera.name)}</span><video muted playsinline preload="metadata" src="${mediaUrl(camera.videoPath)}"></video><canvas width="${camera.metadata?.width || 800}" height="${camera.metadata?.height || 450}"></canvas></div>`).join("") + `<div class="review-feed review-map-feed"><span class="feed-label">SHARED FLOOR PLAN</span><canvas id="reviewMapCanvas" width="1000" height="650"></canvas></div>`;
    $$(".review-feed[data-camera-id]", grid).forEach(feed => {
      const video = $("video", feed);
      video.addEventListener("error", () => toast("A camera video could not be loaded", true));
      video.addEventListener("seeked", () => { if (state.view === "review") drawReviewOverlays(); });
      if (video.requestVideoFrameCallback) {
        const presented = (_now, metadata) => {
          video._presentedMediaTime = metadata.mediaTime;
          if (state.view === "review" && !state.reviewPlaying) drawReviewOverlays();
          video.requestVideoFrameCallback(presented);
        };
        video.requestVideoFrameCallback(presented);
      }
      $("canvas", feed).addEventListener("click", event => selectReviewBox(event, feed));
    });
  }
  renderIdentityControls();
  $("#reviewQuickStaff").innerHTML = state.project.staff.map(person => `<option value="${escapeHTML(person.id)}">${escapeHTML(person.name)}${person.photoPath ? "" : " · video-only"}</option>`).join("") || `<option value="">Add a staff actor first</option>`;
  renderHandoffs();
  renderCorrections();
  renderReviewSelection();
  setReviewTime(state.reviewTime, true);
}

function effectiveCameraOffset(camera) {
  const adjustment = resultTimingIsStale() ? 0 : state.results?.spatialSync?.cameras?.find(row => row.cameraId === camera.id)?.adjustmentSeconds || 0;
  return Number(camera.timeOffsetSeconds || 0) + Number(adjustment);
}

function resultTimingIsStale() {
  return Boolean(state.results && state.project.timestampSyncUpdatedAt
    && new Date(state.project.timestampSyncUpdatedAt) > new Date(state.results.sourceGeneratedAt || state.results.generatedAt));
}

function displayedReviewTime(feed) {
  const camera = state.project.cameras.find(item => item.id === feed.dataset.cameraId);
  const video = $("video", feed);
  return effectiveCameraOffset(camera) + Number(video._presentedMediaTime ?? video.currentTime ?? 0);
}

function selectReviewBox(event, feed) {
  const canvas = $("canvas", feed);
  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height);
  const drawnWidth = canvas.width * scale, drawnHeight = canvas.height * scale;
  const x = (event.clientX - rect.left - (rect.width - drawnWidth) / 2) / drawnWidth;
  const y = (event.clientY - rect.top - (rect.height - drawnHeight) / 2) / drawnHeight;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const matches = observationsAt(feed.dataset.cameraId, displayedReviewTime(feed))
    .filter(row => x >= row.bbox[0] && x <= row.bbox[0] + row.bbox[2]
      && y >= row.bbox[1] && y <= row.bbox[1] + row.bbox[3])
    .sort((a, b) => a.bbox[2] * a.bbox[3] - b.bbox[2] * b.bbox[3]);
  if (!matches.length) { toast("No detected box here. Use Cameras to mark a missed person.", true); return; }
  pauseReview();
  state.selectedReviewObservation = matches[0];
  renderReviewSelection();
  drawReviewOverlays();
}

function renderReviewSelection() {
  const row = state.selectedReviewObservation;
  $("#reviewSelection").hidden = !row;
  if (!row) return;
  const camera = state.project.cameras.find(item => item.id === row.cameraId);
  $("#reviewSelectedName").textContent = identityLabel(row.globalId);
  $("#reviewSelectedDetails").textContent = `${row.globalId} · ${camera?.name || row.cameraId} · ${formatClock(row.time)}${row.estimated ? " · estimated seated presence" : ""}`;
  $("#reviewAssignStaff").disabled = !state.project.staff.length;
  $("#reviewPrepareStaffMark").disabled = !state.project.staff.length;
  for (const selector of ["#mergeSource", "#relabelIdentity", "#splitIdentity"]) {
    if ([...$(selector).options].some(option => option.value === row.globalId)) $(selector).value = row.globalId;
  }
  $("#splitCamera").value = row.cameraId;
}

function renderIdentityControls() {
  const observations = state.results.observations || [];
  const grouped = new Map();
  observations.forEach(observation => {
    const entry = grouped.get(observation.globalId) || { id: observation.globalId, role: observation.role, count: 0, cameras: new Set(), first: observation.time, confidence: [] };
    entry.count += 1; entry.cameras.add(observation.cameraId); entry.first = Math.min(entry.first, observation.time);
    if (observation.identityConfidence > 0) entry.confidence.push(observation.identityConfidence);
    if (observation.role === "staff") entry.role = "staff";
    grouped.set(observation.globalId, entry);
  });
  const identities = [...grouped.values()].sort((a, b) => a.role.localeCompare(b.role) || a.first - b.first);
  $("#identityCount").textContent = identities.length;
  $("#identityList").innerHTML = identities.length ? identities.map((entry, index) => {
    const confidence = entry.confidence.length ? entry.confidence.reduce((sum, value) => sum + value, 0) / entry.confidence.length : null;
    return `<div class="identity-row"><span class="identity-color" style="background:${pathColor(entry.id, index)}"></span><div><strong>${escapeHTML(identityLabel(entry.id))}</strong><span>${entry.role} · ${entry.cameras.size} camera${entry.cameras.size === 1 ? "" : "s"} · ${entry.count} observations${confidence == null ? "" : ` · ${Math.round(confidence * 100)}% face match`}</span></div><button data-time="${entry.first}" data-identity="${escapeHTML(entry.id)}">View</button></div>`;
  }).join("") : `<div class="audit-empty">No stable identities were detected.</div>`;
  $$(".identity-row button", $("#identityList")).forEach(button => button.addEventListener("click", () => {
    setReviewTime(Number(button.dataset.time), true);
    $("#splitIdentity").value = button.dataset.identity;
    $("#relabelIdentity").value = button.dataset.identity;
  }));

  const options = identities.map(entry => `<option value="${escapeHTML(entry.id)}">${escapeHTML(identityLabel(entry.id))}</option>`).join("");
  ["#mergeSource", "#mergeTarget", "#relabelIdentity", "#splitIdentity"].forEach(selector => $(selector).innerHTML = options || `<option value="">No identities</option>`);
  if (identities.length > 1) $("#mergeTarget").selectedIndex = 1;
  $("#relabelStaff").innerHTML = state.project.staff.map(person => `<option value="${person.id}">${escapeHTML(person.name)}</option>`).join("") || `<option value="">No staff enrolled</option>`;
  $("#splitCamera").innerHTML = state.project.cameras.map(camera => `<option value="${camera.id}">${escapeHTML(camera.name)}</option>`).join("");
}

function renderHandoffs() {
  const cameras = new Map(state.project.cameras.map(camera => [camera.id, camera.name]));
  const handoffs = state.results.handoffs || [];
  $("#handoffList").innerHTML = handoffs.length ? handoffs.map(handoff => `<div class="audit-row"><strong>${escapeHTML(identityLabel(handoff.globalId))}: ${escapeHTML(cameras.get(handoff.fromCameraId) || handoff.fromCameraId)} → ${escapeHTML(cameras.get(handoff.toCameraId) || handoff.toCameraId)}${handoff.reviewRequired ? " · REVIEW" : ""}</strong><span>${formatClock(handoff.time)} · ${escapeHTML(handoff.routeName || "Automatic route")} · ${escapeHTML(handoff.basis)} · ${Math.round((handoff.matchConfidence || 0) * 100)}% match confidence · ${Math.round((handoff.distance || 0) * 100)} map units${handoff.mode === "overlap" ? " · simultaneous views" : ` · ${handoff.timeGapSeconds}s gap`}${handoff.bodySimilarity == null ? "" : ` · ${Math.round(handoff.bodySimilarity * 100)}% body similarity`}${handoff.faceSimilarity == null ? "" : ` · ${Math.round(handoff.faceSimilarity * 100)}% face similarity`}</span></div>`).join("") : `<div class="audit-empty">No cross-camera handoffs were created in this run.</div>`;
}

function renderCorrections() {
  const corrections = [...(state.results.corrections || [])].reverse();
  $("#correctionList").innerHTML = corrections.length ? corrections.map(item => `<div class="audit-row"><strong>${item.action[0].toUpperCase() + item.action.slice(1)} correction</strong><span>${new Date(item.createdAt).toLocaleString()} · ${item.observationsChanged || 0} observations changed</span></div>`).join("") : `<div class="audit-empty">No corrections yet. Every change will appear here.</div>`;
}

function setReviewTime(value, force = false) {
  state.reviewTime = Math.max(0, Math.min(Number(value) || 0, state.reviewDuration || 0));
  $("#reviewTimeline").value = state.reviewTime;
  $("#reviewCurrentTime").textContent = formatClock(state.reviewTime);
  $("#splitTimeLabel").textContent = formatClock(state.reviewTime);
  $$(".review-feed[data-camera-id]").forEach(feed => {
    const camera = state.project.cameras.find(item => item.id === feed.dataset.cameraId);
    const video = $("video", feed);
    const localTime = state.reviewTime - effectiveCameraOffset(camera);
    const inRange = localTime >= 0 && localTime <= Number(camera?.metadata?.durationSeconds || video.duration || 0);
    feed.style.opacity = inRange ? "1" : ".28";
    if (!inRange) { if (!video.paused) video.pause(); return; }
    if (force || Math.abs(video.currentTime - localTime) > .12) video.currentTime = localTime;
    if (state.reviewPlaying && video.paused) video.play().catch(() => {});
  });
  drawReviewOverlays();
}

function drawReviewOverlays() {
  $$(".review-feed[data-camera-id]").forEach((feed, feedIndex) => {
    const camera = state.project.cameras.find(item => item.id === feed.dataset.cameraId);
    const canvas = $("canvas", feed), ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const localGlobalTime = displayedReviewTime(feed);
    observationsAt(camera.id, localGlobalTime).forEach((observation, index) => {
      const [x, y, width, height] = observation.bbox;
      const color = observation.needsReview || observation.role === "unresolved" || observation.identityConfidence > 0 && observation.identityConfidence < .45 ? "#e0a63d" : observation.role === "staff" ? "#ff745e" : "#55d48b";
      ctx.strokeStyle = color; ctx.lineWidth = Math.max(2, canvas.width / 400);
      ctx.setLineDash(observation.estimated ? [12, 8] : []);
      ctx.strokeRect(x * canvas.width, y * canvas.height, width * canvas.width, height * canvas.height);
      ctx.setLineDash([]);
      if (state.selectedReviewObservation === observation) {
        ctx.strokeStyle = "#fff24b"; ctx.lineWidth = Math.max(4, canvas.width / 220);
        ctx.strokeRect(x * canvas.width - 3, y * canvas.height - 3, width * canvas.width + 6, height * canvas.height + 6);
      }
      const label = `${identityLabel(observation.globalId)}${observation.tableId ? ` · T${state.project.tables.find(table => table.id === observation.tableId)?.number || "?"}` : ""}`;
      ctx.font = `700 ${Math.max(11, canvas.width / 70)}px system-ui`;
      const labelWidth = ctx.measureText(label).width + 12;
      ctx.fillStyle = color; ctx.fillRect(x * canvas.width, Math.max(0, y * canvas.height - 24), labelWidth, 23);
      ctx.fillStyle = "#102017"; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(label, x * canvas.width + 6, Math.max(11, y * canvas.height - 12));
    });
  });
  const map = $("#reviewMapCanvas");
  if (map) {
    paintMap(map);
    const ctx = map.getContext("2d");
    const current = $$(".review-feed[data-camera-id]").flatMap(feed => observationsAt(feed.dataset.cameraId, displayedReviewTime(feed))).filter(item => item.map);
    const unique = new Map(); current.forEach(item => unique.set(item.globalId, item));
    [...unique.values()].forEach((observation, index) => {
      const x = observation.map[0] * map.width, y = observation.map[1] * map.height;
      marker(ctx, x, y, identityLabel(observation.globalId).replace("Customer ", "C"), observation.needsReview || observation.role === "unresolved" ? "#e0a63d" : observation.role === "staff" ? "#ff745e" : "#2e9961");
    });
  }
}

function formatClock(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60), secs = Math.floor(seconds % 60);
  return `${hours ? `${String(hours).padStart(2, "0")}:` : ""}${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function playReview() {
  if (!state.reviewDuration) return;
  if (state.reviewTime >= state.reviewDuration) state.reviewTime = 0;
  state.reviewPlaying = true; state.reviewBaseTime = state.reviewTime; state.reviewStartedAt = performance.now();
  $("#reviewPlayButton").textContent = "Pause";
  setReviewTime(state.reviewTime, true);
  const tick = now => {
    if (!state.reviewPlaying) return;
    const next = state.reviewBaseTime + (now - state.reviewStartedAt) / 1000;
    if (next >= state.reviewDuration) { setReviewTime(state.reviewDuration, true); pauseReview(); return; }
    setReviewTime(next, false); state.reviewFrame = requestAnimationFrame(tick);
  };
  state.reviewFrame = requestAnimationFrame(tick);
}

function pauseReview() {
  state.reviewPlaying = false; $("#reviewPlayButton").textContent = "Play";
  if (state.reviewFrame) cancelAnimationFrame(state.reviewFrame);
  $$(".review-feed video").forEach(video => video.pause());
}

async function submitCorrection(payload, button) {
  button.disabled = true;
  try {
    const value = await api("/api/corrections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    state.results = value.results; state.selectedReviewObservation = null;
    renderReview(); renderReports(); toast("Correction saved and reports recalculated");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

function drawReportMap() {
  if (!state.project) return;
  paintMap($("#reportMapCanvas"), { trajectories: state.results?.trajectories || null });
}

function renderAll() {
  renderOverview(); renderTables(); renderZones(); renderCameras(); renderStaff(); renderReadiness(); renderReview(); renderReports(); drawLayoutEditor();
}

async function pollJob(jobId) {
  $("#progressCard").hidden = false;
  $("#analyzeButton").disabled = true;
  while (true) {
    const job = await api(`/api/jobs/${jobId}`);
    const progress = Math.round((job.progress || 0) * 100);
    $("#progressMessage").textContent = job.message || "Analyzing";
    $("#progressPercent").textContent = `${progress}%`;
    $("#progressBar").style.width = `${progress}%`;
    if (job.status === "complete") {
      const [response, project] = await Promise.all([api("/api/results"), api("/api/project")]);
      state.results = response.results; state.project = project;
      renderAll(); toast(job.message || "Analysis complete"); showView("reports"); return;
    }
    if (job.status === "error") { renderReadiness(); throw new Error(job.message || "Analysis failed"); }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

function bindEvents() {
  $$(".nav-item[data-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => showView(button.dataset.go)));
  $("#helpButton").addEventListener("click", () => $("#helpDialog").showModal());
  $("#helpDialog .dialog-close").addEventListener("click", () => $("#helpDialog").close());

  $("#layoutUpload").addEventListener("change", event => {
    const file = event.target.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => { state.project.restaurant.layoutImage = reader.result; loadLayoutImage(); await saveProject(true); toast("Floor plan added"); };
    reader.readAsDataURL(file);
  });
  $("#clearLayoutButton").addEventListener("click", () => { state.project.restaurant.layoutImage = null; state.layoutImage = null; saveProject(); drawLayoutEditor(); drawCalibrationMap(); });
  $("#addTableButton").addEventListener("click", () => {
    state.drawTable = !state.drawTable;
    state.drawZoneType = null;
    $("#layoutCanvas").classList.toggle("draw-mode", state.drawTable);
    $("#addTableButton").textContent = state.drawTable ? "Cancel drawing" : "Draw a table";
    $("#addZoneButton").textContent = "Draw a zone";
    $("#layoutModeLabel").textContent = state.drawTable ? "Drag around the table and its chairs" : "Select “Draw a table”, then drag on the plan";
  });
  $("#addZoneButton").addEventListener("click", () => {
    state.drawZoneType = state.drawZoneType ? null : $("#zoneTypeSelect").value;
    state.drawTable = false;
    $("#layoutCanvas").classList.toggle("draw-mode", !!state.drawZoneType);
    $("#addTableButton").textContent = "Draw a table";
    $("#addZoneButton").textContent = state.drawZoneType ? "Cancel zone" : "Draw a zone";
    $("#layoutModeLabel").textContent = state.drawZoneType ? `Drag to mark a ${$("#zoneTypeSelect").selectedOptions[0].text.toLowerCase()}` : "Select a drawing tool, then drag on the plan";
  });
  $("#zoneTypeSelect").addEventListener("change", event => { if (state.drawZoneType) { state.drawZoneType = event.target.value; $("#layoutModeLabel").textContent = `Drag to mark a ${event.target.selectedOptions[0].text.toLowerCase()}`; } });
  const layoutCanvas = $("#layoutCanvas");
  layoutCanvas.addEventListener("pointerdown", event => { if (!state.drawTable && !state.drawZoneType) return; const point = eventPoint(layoutCanvas, event); state.tableDrag = { start: point, end: point }; layoutCanvas.setPointerCapture(event.pointerId); });
  layoutCanvas.addEventListener("pointermove", event => { if (!state.tableDrag) return; state.tableDrag.end = eventPoint(layoutCanvas, event); drawLayoutEditor(); });
  layoutCanvas.addEventListener("pointerup", () => {
    if (!state.tableDrag) return;
    const drag = state.tableDrag; state.tableDrag = null;
    const x = Math.min(drag.start.x, drag.end.x), y = Math.min(drag.start.y, drag.end.y), w = Math.abs(drag.end.x - drag.start.x), h = Math.abs(drag.end.y - drag.start.y);
    if (w >= .025 && h >= .025) {
      if (state.drawTable) {
        const numeric = state.project.tables.map(table => Number.parseInt(table.number, 10)).filter(Number.isFinite);
        state.project.tables.push({ id: uid("table"), number: String((numeric.length ? Math.max(...numeric) : 0) + 1).padStart(2, "0"), x, y, w, h });
      } else if (state.drawZoneType) {
        const type = state.drawZoneType;
        const labels = { seat: "Seat", handoff: "Handoff", entrance: "Entrance", waiting: "Waiting", staff: "Staff area" };
        const sameType = (state.project.zones || []).filter(zone => zone.type === type).length;
        let tableId = null;
        if (type === "seat" && state.project.tables.length) {
          const center = { x: x + w / 2, y: y + h / 2 };
          tableId = [...state.project.tables].sort((left, right) => Math.hypot(center.x - (left.x + left.w / 2), center.y - (left.y + left.h / 2)) - Math.hypot(center.x - (right.x + right.w / 2), center.y - (right.y + right.h / 2)))[0].id;
        }
        state.project.zones ||= [];
        state.project.zones.push({ id: uid("zone"), name: `${labels[type]} ${sameType + 1}`, type, tableId, x, y, w, h });
      }
      saveProject(); renderTables(); renderZones(); renderCameraLinks();
    }
    state.drawTable = false; state.drawZoneType = null; layoutCanvas.classList.remove("draw-mode"); $("#addTableButton").textContent = "Draw a table"; $("#addZoneButton").textContent = "Draw a zone"; $("#layoutModeLabel").textContent = "Select a drawing tool, then drag on the plan"; drawLayoutEditor();
  });

  $("#cameraForm").addEventListener("submit", async event => {
    event.preventDefault(); const button = $('button[type="submit"]', event.target); button.disabled = true; button.textContent = "Adding video…";
    try { const value = await api("/api/cameras", { method: "POST", body: new FormData(event.target) }); state.project = value.project; state.selectedCameraId = value.camera.id; event.target.reset(); renderAll(); toast("Camera added—match its floor points next"); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = "Add camera"; }
  });
  $("#syncTimestampsButton").addEventListener("click", async event => {
    const button = event.target; button.disabled = true; button.textContent = "Reading camera clocks…";
    try {
      const value = await api("/api/timestamps/sync", { method: "POST" });
      state.project = value.project; renderCameras(); renderReports();
      const verified = value.timestampSync.cameras.filter(row => row.status === "verified").length;
      toast(value.timestampSync.applied ? `${verified} camera clocks checked; offsets updated. Rerun analysis.` : `${verified} camera clocks checked; existing offsets kept.`);
    } catch (error) { toast(error.message, true); }
    finally { button.textContent = "Sync camera times"; button.disabled = false; }
  });
  $("#timestampSyncList").addEventListener("change", async event => {
    const input = event.target.closest("[data-clock-adjust-camera]");
    if (!input) return;
    const camera = state.project.cameras.find(item => item.id === input.dataset.clockAdjustCamera);
    const updated = Number(input.value);
    if (!camera || !Number.isFinite(updated) || Math.abs(updated) > 30) {
      toast("Choose a fine shift between -30 and 30 seconds", true); renderCameraClocks(); return;
    }
    const previous = Number(camera.manualTimeAdjustmentSeconds || 0);
    camera.manualTimeAdjustmentSeconds = updated;
    camera.timeOffsetSeconds = Math.round((Number(camera.timeOffsetSeconds || 0) + updated - previous) * 100) / 100;
    state.project.timestampSyncUpdatedAt = new Date().toISOString();
    await saveProject(true); renderCameras(); renderReports(); renderReview();
    toast("Camera time adjusted. Rerun analysis for corrected identities and metrics.");
  });
  $("#manualCameraSelect").addEventListener("change", event => {
    state.manualCameraId = event.target.value; state.manualBox = null;
    renderManualMarks();
  });
  $("#manualVideo").addEventListener("timeupdate", event => {
    $("#manualTimeline").value = event.target.currentTime;
    $("#manualCurrentTime").textContent = formatClock(event.target.currentTime);
    drawManualCanvas();
  });
  $("#manualVideo").addEventListener("pause", () => $("#manualPlayButton").textContent = "Play");
  $("#manualVideo").addEventListener("play", () => $("#manualPlayButton").textContent = "Pause");
  $("#manualPlayButton").addEventListener("click", () => {
    const video = $("#manualVideo");
    if (!manualCamera()) return;
    if (video.paused) video.play().catch(() => toast("Could not play this video", true)); else video.pause();
  });
  $("#manualTimeline").addEventListener("input", event => {
    const video = $("#manualVideo"); video.pause(); video.currentTime = Number(event.target.value);
    $("#manualCurrentTime").textContent = formatClock(video.currentTime);
    drawManualCanvas();
  });
  $("#manualRole").addEventListener("change", event => $("#manualStaffWrap").hidden = event.target.value !== "staff");
  const manualCanvas = $("#manualCanvas");
  manualCanvas.addEventListener("pointerdown", event => {
    if (!manualCamera()) return;
    const video = $("#manualVideo"); video.pause();
    const point = eventPoint(manualCanvas, event);
    state.manualDrag = { start: point, end: point };
    manualCanvas.setPointerCapture(event.pointerId);
    drawManualCanvas();
  });
  manualCanvas.addEventListener("pointermove", event => {
    if (!state.manualDrag) return;
    state.manualDrag.end = eventPoint(manualCanvas, event); drawManualCanvas();
  });
  manualCanvas.addEventListener("pointerup", event => {
    if (!state.manualDrag) return;
    state.manualDrag.end = eventPoint(manualCanvas, event);
    const { start, end } = state.manualDrag; state.manualDrag = null;
    const box = [Math.min(start.x, end.x), Math.min(start.y, end.y), Math.abs(end.x - start.x), Math.abs(end.y - start.y)];
    if (box[2] < .015 || box[3] < .015) { toast("Drag a larger box around one person", true); drawManualCanvas(); return; }
    state.manualBox = box.map(value => Number(value.toFixed(5)));
    const current = Number($("#manualVideo").currentTime || 0);
    const duration = Number(manualCamera()?.metadata?.durationSeconds || $("#manualVideo").duration || 0);
    $("#manualStart").value = current.toFixed(1);
    $("#manualEnd").value = Math.min(duration, current + 30).toFixed(1);
    $("#manualDrawStatus").textContent = "Box ready. Set the time range and save.";
    drawManualCanvas();
  });
  $("#manualMarkForm").addEventListener("submit", async event => {
    event.preventDefault();
    const camera = manualCamera();
    if (!camera || !state.manualBox) { toast("Pause the camera and draw a person box first", true); return; }
    const role = $("#manualRole").value;
    const staffId = role === "staff" ? $("#manualStaff").value : null;
    if (role === "staff" && !staffId) { toast("Enroll or choose a staff actor first", true); return; }
    const startSeconds = Number($("#manualStart").value), endSeconds = Number($("#manualEnd").value);
    const duration = Number(camera.metadata?.durationSeconds || 0);
    if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds || startSeconds < 0 || endSeconds > duration + .5) {
      toast("Set a valid start and end within the camera recording", true); return;
    }
    state.project.manualMarks ||= [];
    state.project.manualMarks.push({ id: uid("mark"), cameraId: camera.id, role, staffId,
      tableId: $("#manualTable").value || null, bbox: state.manualBox,
      startSeconds, endSeconds });
    await saveProject(true);
    state.manualBox = null; $("#manualDrawStatus").textContent = "Saved. Drag another person box, or run analysis again.";
    renderManualMarks(); toast("Person marking saved for the next analysis");
  });
  $("#calibrationCameraSelect").addEventListener("change", event => { state.selectedCameraId = event.target.value; state.pendingImagePoint = null; state.placingCamera = false; drawCalibration(); });
  $("#cameraCanvas").addEventListener("click", event => {
    if (!selectedCamera()) return;
    state.placingCamera = false; state.pendingImagePoint = eventPoint($("#cameraCanvas"), event); drawCalibration();
  });
  $("#calibrationMapCanvas").addEventListener("click", async event => {
    const camera = selectedCamera(); if (!camera) return;
    const point = eventPoint($("#calibrationMapCanvas"), event);
    if (state.placingCamera) { camera.mapPosition = [point.x, point.y]; state.placingCamera = false; await saveProject(true); drawCalibration(); return; }
    if (!state.pendingImagePoint) { toast("Click the matching point in the camera image first", true); return; }
    camera.calibration.push({ image: [state.pendingImagePoint.x, state.pendingImagePoint.y], map: [point.x, point.y] });
    state.pendingImagePoint = null; await saveProject(true); renderCameras(); drawCalibration();
  });
  $("#undoCalibration").addEventListener("click", async () => { const camera = selectedCamera(); if (!camera) return; if (state.pendingImagePoint) state.pendingImagePoint = null; else camera.calibration.pop(); await saveProject(true); renderCameras(); drawCalibration(); });
  $("#placeCameraButton").addEventListener("click", () => { if (!selectedCamera()) return; state.placingCamera = true; state.pendingImagePoint = null; renderCalibrationPoints(); });
  $("#routeMode").addEventListener("change", event => {
    if (event.target.value === "overlap") {
      $("#routeMinSeconds").value = -1;
      $("#routeMaxSeconds").value = 1;
    } else {
      $("#routeMinSeconds").value = 0;
      $("#routeMaxSeconds").value = state.project.settings.handoffSeconds ?? 8;
    }
  });
  $("#cameraLinkForm").addEventListener("submit", async event => {
    event.preventDefault();
    const fromCameraId = $("#routeFromCamera").value;
    const toCameraId = $("#routeToCamera").value;
    if (!fromCameraId || !toCameraId || fromCameraId === toCameraId) {
      toast("Choose two different cameras", true);
      return;
    }
    const duplicate = (state.project.cameraLinks || []).some(route => route.fromCameraId === fromCameraId && route.toCameraId === toCameraId);
    if (duplicate) {
      toast("That camera direction is already configured", true);
      return;
    }
    const fromName = state.project.cameras.find(camera => camera.id === fromCameraId)?.name || "Camera";
    const toName = state.project.cameras.find(camera => camera.id === toCameraId)?.name || "Camera";
    const mode = $("#routeMode").value;
    const route = {
      id: uid("route"),
      name: `${fromName} to ${toName}`,
      fromCameraId,
      toCameraId,
      mode,
      exitZoneId: $("#routeExitZone").value || null,
      entryZoneId: $("#routeEntryZone").value || null,
      minSeconds: Number($("#routeMinSeconds").value),
      maxSeconds: Number($("#routeMaxSeconds").value),
      maxDistance: Number($("#routeMaxDistance").value),
      bidirectional: $("#routeBidirectional").checked,
    };
    if (!Number.isFinite(route.minSeconds) || !Number.isFinite(route.maxSeconds) || route.maxSeconds < route.minSeconds || !Number.isFinite(route.maxDistance) || route.maxDistance <= 0) {
      toast("Check the route timing and map distance", true);
      return;
    }
    state.project.cameraLinks ||= [];
    state.project.cameraLinks.push(route);
    await saveProject(true);
    renderCameraLinks();
    toast(mode === "overlap" ? "Overlapping camera route added" : "Camera handoff route added");
  });

  $("#staffForm").addEventListener("submit", async event => {
    event.preventDefault(); const button = $('button[type="submit"]', event.target); button.disabled = true; button.textContent = "Creating profile…";
    try { const value = await api("/api/staff", { method: "POST", body: new FormData(event.target) }); state.project = value.project; event.target.reset(); renderAll(); toast(value.staff.photoPath ? "Staff face profile created" : "Video-only staff profile created; identify them in Review"); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = "Create staff profile"; }
  });

  [["#sampleFps", "sampleFps"], ["#tableRadius", "tableRadius"], ["#handoffDistance", "handoffDistance"], ["#handoffSeconds", "handoffSeconds"], ["#bodyReidThreshold", "bodyReidThreshold"], ["#staffThreshold", "staffFaceThreshold"], ["#customerThreshold", "customerFaceThreshold"]].forEach(([selector, key]) => $(selector).addEventListener("change", event => { state.project.settings[key] = Number(event.target.value); saveProject(); }));
  $("#analyzeButton").addEventListener("click", async () => {
    try { await saveProject(true); const job = await api("/api/analyze", { method: "POST" }); await pollJob(job.id); }
    catch (error) { toast(error.message, true); renderReadiness(); }
  });
  $("#exportButton").addEventListener("click", () => {
    if (!state.results) return;
    const blob = new Blob([JSON.stringify(state.results, null, 2)], { type: "application/json" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `restaurant-vision-${new Date().toISOString().slice(0, 10)}.json`; link.click(); URL.revokeObjectURL(link.href);
  });
  $("#exportTrainingButton").addEventListener("click", () => {
    if (!state.results) return;
    const manifest = {
      schema: "serviceflow-training-labels/v1",
      exportedAt: new Date().toISOString(),
      runId: state.results.runId,
      cameraSources: state.project.cameras.map(camera => ({ id: camera.id, name: camera.name, videoPath: camera.videoPath, timeOffsetSeconds: camera.timeOffsetSeconds, calibration: camera.calibration, calibrationQuality: camera.calibrationQuality })),
      layout: { tables: state.project.tables, zones: state.project.zones || [], cameraLinks: state.project.cameraLinks || [] },
      manualMarks: state.project.manualMarks || [],
      identities: [...new Set(state.results.observations.map(item => item.globalId))].map(id => ({ id, label: identityLabel(id), role: id.startsWith("staff:") ? "staff" : "customer" })),
      observations: state.results.observations,
      tracklets: state.results.tracklets,
      handoffs: state.results.handoffs || [],
      humanCorrections: state.results.corrections || [],
    };
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `serviceflow-training-labels-${state.results.runId || "latest"}.json`; link.click(); URL.revokeObjectURL(link.href);
  });
  $("#reviewPlayButton").addEventListener("click", () => state.reviewPlaying ? pauseReview() : playReview());
  $("#reviewTimeline").addEventListener("input", event => { pauseReview(); setReviewTime(Number(event.target.value), true); });
  $("#reviewSelectForCorrection").addEventListener("click", () => {
    if (!state.selectedReviewObservation) return;
    renderReviewSelection();
    $(".correction-card").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#reviewAssignStaff").addEventListener("click", event => {
    const row = state.selectedReviewObservation;
    if (!row || !$("#reviewQuickStaff").value) return;
    submitCorrection({ action: "relabel", identityId: row.globalId,
      role: "staff", staffId: $("#reviewQuickStaff").value }, event.currentTarget);
  });
  $("#reviewPrepareStaffMark").addEventListener("click", () => {
    const row = state.selectedReviewObservation;
    const staffId = $("#reviewQuickStaff").value;
    if (!row || !staffId) return;
    const camera = state.project.cameras.find(item => item.id === row.cameraId);
    if (!camera) return;
    state.manualCameraId = row.cameraId;
    renderManualMarks();
    state.manualBox = [...row.bbox];
    $("#manualRole").value = "staff";
    $("#manualStaffWrap").hidden = false;
    $("#manualStaff").value = staffId;
    $("#manualTable").value = row.tableId || "";
    const localTime = Math.max(0, row.time - effectiveCameraOffset(camera));
    const duration = Number(camera.metadata?.durationSeconds || 0);
    $("#manualStart").value = localTime.toFixed(1);
    $("#manualEnd").value = Math.min(duration, localTime + 30).toFixed(1);
    $("#manualDrawStatus").textContent = "Box copied from Review. Check when the actor moves or leaves, then save.";
    const video = $("#manualVideo");
    if (video.readyState >= 1) video.currentTime = localTime;
    else video.addEventListener("loadedmetadata", () => { video.currentTime = localTime; drawManualCanvas(); }, { once: true });
    $("#manualTimeline").value = localTime;
    $("#manualCurrentTime").textContent = formatClock(localTime);
    drawManualCanvas();
    showView("cameras");
    $("#manualPanel").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#reviewAssignCustomer").addEventListener("click", event => {
    const row = state.selectedReviewObservation;
    if (!row) return;
    submitCorrection({ action: "relabel", identityId: row.globalId, role: "customer" }, event.currentTarget);
  });
  $("#relabelRole").addEventListener("change", event => $("#relabelStaffWrap").hidden = event.target.value !== "staff");
  $("#mergeForm").addEventListener("submit", event => {
    event.preventDefault();
    submitCorrection({ action: "merge", sourceId: $("#mergeSource").value, targetId: $("#mergeTarget").value }, $('button[type="submit"]', event.target));
  });
  $("#relabelForm").addEventListener("submit", event => {
    event.preventDefault();
    submitCorrection({ action: "relabel", identityId: $("#relabelIdentity").value, role: $("#relabelRole").value, staffId: $("#relabelStaff").value }, $('button[type="submit"]', event.target));
  });
  $("#splitForm").addEventListener("submit", event => {
    event.preventDefault();
    submitCorrection({ action: "split", identityId: $("#splitIdentity").value, cameraId: $("#splitCamera").value, time: state.reviewTime }, $('button[type="submit"]', event.target));
  });
}

async function init() {
  bindEvents();
  try {
    const [project, health, report] = await Promise.all([api("/api/project"), api("/api/health"), api("/api/results")]);
    state.project = project; state.results = report.available ? report.results : null;
    const modelReady = Object.values(health.models).every(Boolean);
    $("#systemDot").classList.toggle("ok", modelReady); $("#systemStatus").textContent = modelReady ? "Vision models ready" : "Model files missing";
    loadLayoutImage(); renderAll();
    const params = new URLSearchParams(location.search);
    if (params.get("time")) state.reviewTime = Number(params.get("time")) || 0;
    const requestedView = params.get("view");
    if (requestedView && viewTitles[requestedView]) showView(requestedView);
  } catch (error) {
    toast(`Could not start: ${error.message}`, true); $("#systemStatus").textContent = "Server unavailable";
  }
}

init();
