const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let state = null;
let ports = [];
let profiles = [];
let socket = null;
let lastMessage = "";
let toastTimer = null;
let selectedHistoryId = null;
let activeView = "bus";
let jogPending = false;
let groupJogPending = false;
let centerCalibrationPending = false;
let groupConfigPending = false;
let lastBusRenderAt = 0;
let lastMotorListSignature = "";
let lastGroupConfigBusSignature = "";
let idStationEventSignature = "";
const currentPeaks = new Map();
const history = [];
const MAX_HISTORY = 300;
const controlDrafts = new Map();
const groupConfigDirty = new Set();
const THEME_STORAGE_KEY = "nori-motorlab-theme";
const CONFIRMATION_STORAGE_KEY = "nori-motorlab-skipped-confirmations";
const GROUP_CONFIG_FIELDS = [
  { key: "p_gain", element: "groupConfigP", label: "P gain" },
  { key: "d_gain", element: "groupConfigD", label: "D gain" },
  { key: "i_gain", element: "groupConfigI", label: "I gain" },
  { key: "min_position", element: "groupConfigMinPosition", label: "minimum position" },
  { key: "max_position", element: "groupConfigMaxPosition", label: "maximum position" },
  { key: "max_temperature", element: "groupConfigMaxTemp", label: "maximum temperature" },
  { key: "min_voltage", element: "groupConfigMinVoltage", label: "minimum voltage" },
  { key: "max_voltage", element: "groupConfigMaxVoltage", label: "maximum voltage" }
];
const CONTROL_MODES = {
  0: {
    name: "Angle servo", label: "TARGET POSITION", min: 0, max: 4095, neutral: 2048,
    minLabel: "0 / 0°", maxLabel: "4095 / 360°", neutralLabel: "SET CENTER · 2048",
    action: "GO TO TARGET", readout: "TARGET", unit: "RAW",
    help: "Absolute 0–360° position control with velocity and acceleration limits."
  },
  1: {
    name: "Closed-loop speed", label: "SIGNED SPEED SETPOINT", min: -3073, max: 3073, neutral: 0,
    minLabel: "−3073 / DIR −", maxLabel: "+3073 / DIR +", neutralLabel: "STOP · 0",
    action: "RUN AT SPEED", readout: "SPEED CMD", unit: "RAW",
    help: "Regulated continuous rotation. The servo adjusts output to hold the signed speed setpoint."
  },
  2: {
    name: "Open-loop speed", label: "SIGNED DRIVE LEVEL", min: -1000, max: 1000, neutral: 0,
    minLabel: "−1000 / DIR −", maxLabel: "+1000 / DIR +", neutralLabel: "STOP · 0",
    action: "RUN OPEN LOOP", readout: "DRIVE CMD", unit: "RAW",
    help: "Unregulated continuous rotation using Feetech's signed open-loop drive parameter."
  },
  3: {
    name: "Relative step", label: "RELATIVE STEP", min: -4095, max: 4095, neutral: 0,
    minLabel: "−4095 STEPS", maxLabel: "+4095 STEPS", neutralLabel: "ZERO STEP · 0",
    action: "MOVE RELATIVE", readout: "STEP CMD", unit: "STEPS",
    help: "Moves by a signed number of encoder steps from the current position each time it is sent."
  }
};

const ids = [
  "themeButton", "themeText", "resetConfirmationsButton", "resetConfirmationsText", "brandHome", "recordButton", "recordText", "connectionBadge", "connectionText", "portSelect", "baudSelect",
  "scanButton", "connectButton", "refreshPorts", "motorCount", "motorList", "busOverviewButton", "welcomeState", "busDashboard", "dashboard",
  "idStationNavButton", "welcomeIdStationButton", "idStationView", "idStationBackButton", "idStationStatusChip", "idModeSingle", "idModeSequential", "idStationPortSelect", "idStationBaudSelect", "idStationModelSelect", "idSingleFields", "idSequentialFields", "idSingleTarget", "idSequenceStart", "idSequenceEnd", "idSequenceLoop", "idStationStartButton", "idStationPhaseLabel", "idStationTarget", "idStationCompleted", "idStationMessage", "idStationDetected", "idStationSimulationControls", "idSimPlugButton", "idSimUnplugButton", "idStationStopButton", "idStationEventLog",
  "busEstopButton", "busOnlineCount", "busLinkSummary", "busArmedCount", "busMovingCount", "busTotalCurrent", "busTotalTorque",
  "busPeakTemp", "busVoltageRange", "busFaultCount", "busCommCount", "busChainMeta", "busChainNodes", "busUpdatedAt", "busMotorCards", "backToBusButton",
  "groupControlStatus", "groupTorqueButton", "groupTorqueButtonText", "groupCenterEncoderButton", "groupTargetInput", "groupTargetSlider", "groupJogMinusButton", "groupJogPlusButton", "groupJogStepSelect", "groupJogHint", "groupVelocitySlider", "groupVelocityOutput", "groupAccelerationSlider", "groupAccelerationOutput", "groupTorqueSlider", "groupTorqueOutput", "groupCommandButton",
  "groupConfigState", "groupConfigP", "groupConfigD", "groupConfigI", "groupConfigMinPosition", "groupConfigMaxPosition", "groupConfigMaxTemp", "groupConfigMinVoltage", "groupConfigMaxVoltage", "groupConfigDirtySummary", "groupWriteConfigButton",
  "headerStatus", "headerModel", "headerId", "headerVariant", "datasheetLink", "torqueButton", "estopButton", "centerEncoderButton",
  "positionDial", "positionDegrees", "positionRaw", "targetReadoutLabel", "targetReadout", "targetReadoutUnit", "errorReadout", "motionReadout", "modeReadout",
  "velocityValue", "rpmValue", "loadValue", "loadRaw", "voltageValue", "voltageState", "temperatureValue",
  "temperatureState", "currentValue", "currentRaw", "torqueEstimate", "deviceStatus", "commErrors", "sampleStatus", "traceChart", "targetInput",
  "targetSlider", "targetInputUnit", "motionCommandLabel", "commandMinLabel", "commandMaxLabel", "centerButton", "modeSelect", "modeHelp", "applyModeButton", "velocityRow", "velocitySlider", "velocityOutput", "accelerationSlider", "accelerationOutput",
  "torqueSlider", "torqueOutput", "torqueRawOutput", "torqueCurrentOutput", "torquePeakCurrentOutput", "torqueRatedMarker", "torqueRatedLabel", "applyParameters", "goButton", "goButtonText", "torqueHint", "jogMinusButton", "jogPlusButton", "jogStepSelect", "jogHint", "captureButton", "captureList",
  "configP", "configD", "configI", "configMinPosition", "configMaxPosition", "configMaxTemp", "configMinVoltage",
  "configMaxVoltage", "writeConfigButton", "specModel", "specTorque", "specCurrent", "specVoltage", "specTemp",
  "safetyDialog", "safetyDialogEyebrow", "safetyDialogTitle", "safetyDialogMessage", "safetyDialogDetails", "safetyDialogDontAsk", "safetyDialogDontAskLabel", "safetyDialogCancel", "safetyDialogConfirm",
  "toast", "toastMessage", "busyOverlay", "busyText"
];
const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
const skippedConfirmations = loadSkippedConfirmations();

function setTheme(theme, persist = false) {
  const nextTheme = theme === "light" ? "light" : "dark";
  const dark = nextTheme === "dark";
  document.documentElement.dataset.theme = nextTheme;
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0b0e11" : "#fbfaf5";
  el.themeButton.setAttribute("aria-pressed", String(dark));
  el.themeButton.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
  el.themeButton.title = `Switch to ${dark ? "light" : "dark"} mode`;
  el.themeText.textContent = dark ? "DARK" : "LIGHT";
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, nextTheme); } catch (_) {}
  }
}

function loadSkippedConfirmations() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIRMATION_STORAGE_KEY) || "[]");
    return new Set(Array.isArray(saved) ? saved.filter((key) => typeof key === "string") : []);
  } catch (_) {
    return new Set();
  }
}

function saveSkippedConfirmations() {
  try { localStorage.setItem(CONFIRMATION_STORAGE_KEY, JSON.stringify([...skippedConfirmations])); } catch (_) {}
  updateConfirmationPreferenceControl();
}

function updateConfirmationPreferenceControl() {
  const count = skippedConfirmations.size;
  el.resetConfirmationsButton.classList.toggle("hidden", count === 0);
  el.resetConfirmationsButton.setAttribute("aria-label", `Restore ${count} hidden safety confirmation${count === 1 ? "" : "s"}`);
  el.resetConfirmationsButton.title = `Restore ${count} hidden safety confirmation${count === 1 ? "" : "s"}`;
  el.resetConfirmationsText.textContent = count === 1 ? "1 PROMPT" : `${count} PROMPTS`;
}

function restoreSafetyConfirmations() {
  const count = skippedConfirmations.size;
  skippedConfirmations.clear();
  saveSkippedConfirmations();
  toast(`${count} safety confirmation${count === 1 ? "" : "s"} restored`);
}

async function requestConfirmation({ key, eyebrow, title, message, details, confirmLabel, skipLabel, tone = "warning" }) {
  if (skippedConfirmations.has(key)) return true;

  el.safetyDialog.className = `safety-dialog ${tone}`;
  el.safetyDialogEyebrow.textContent = eyebrow;
  el.safetyDialogTitle.textContent = title;
  el.safetyDialogMessage.textContent = message;
  el.safetyDialogDetails.innerHTML = details.map((detail) => `<li>${escapeHtml(detail)}</li>`).join("");
  el.safetyDialogDontAsk.checked = false;
  el.safetyDialogDontAskLabel.textContent = skipLabel;
  el.safetyDialogConfirm.textContent = confirmLabel;
  el.safetyDialog.returnValue = "";

  const approved = await new Promise((resolve) => {
    el.safetyDialog.addEventListener("close", () => resolve(el.safetyDialog.returnValue === "confirm"), { once: true });
    el.safetyDialog.showModal();
    requestAnimationFrame(() => el.safetyDialogCancel.focus());
  });

  if (approved && el.safetyDialogDontAsk.checked) {
    skippedConfirmations.add(key);
    saveSkippedConfirmations();
  }
  return approved;
}

async function api(path, options = {}) {
  const request = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = payload?.detail || payload || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return payload;
}

function setBusy(active, text = "WORKING") {
  el.busyText.textContent = text;
  el.busyOverlay.classList.toggle("hidden", !active);
}

function toast(message, level = "success") {
  clearTimeout(toastTimer);
  el.toast.className = `toast ${level}`;
  el.toastMessage.textContent = message;
  requestAnimationFrame(() => el.toast.classList.add("show"));
  toastTimer = setTimeout(() => el.toast.classList.remove("show"), level === "danger" ? 5500 : 3200);
}

function handleError(error) {
  console.error(error);
  toast(error.message || String(error), "danger");
}

function selectedMotor() {
  return state?.motors?.find((motor) => motor.id === state.selected_id) || null;
}

function setInputValue(input, value) {
  if (document.activeElement !== input && value !== undefined && value !== null) input.value = value;
}

function controlDraft(motor) {
  const serverCommand = motor.operating_mode === 0 ? motor.target_position : motor.mode_command;
  let draft = controlDrafts.get(motor.id);
  if (!draft) {
    draft = {
      target: serverCommand,
      velocity: motor.velocity_limit,
      acceleration: motor.acceleration,
      torque: motor.torque_limit,
      mode: motor.operating_mode,
      appliedMode: motor.operating_mode,
      dirty: { target: false, velocity: false, acceleration: false, torque: false, mode: false }
    };
    controlDrafts.set(motor.id, draft);
  }
  if (draft.appliedMode !== motor.operating_mode) {
    draft.appliedMode = motor.operating_mode;
    draft.target = serverCommand;
    draft.dirty.target = false;
  }
  if (!draft.dirty.target) draft.target = serverCommand;
  if (!draft.dirty.velocity) draft.velocity = motor.velocity_limit;
  if (!draft.dirty.acceleration) draft.acceleration = motor.acceleration;
  if (!draft.dirty.torque) draft.torque = motor.torque_limit;
  if (!draft.dirty.mode) draft.mode = motor.operating_mode;
  return draft;
}

function updateDraft(key, value) {
  const motor = selectedMotor();
  if (!motor) return;
  const draft = controlDraft(motor);
  draft[key] = Number(value);
  draft.dirty[key] = true;
}

function acceptMotorUpdate(updated, keys) {
  if (!state || !updated) return;
  const index = state.motors.findIndex((motor) => motor.id === updated.id);
  if (index >= 0) state.motors[index] = { ...state.motors[index], ...updated };
  const draft = controlDrafts.get(updated.id);
  if (!draft) return;
  keys.forEach((key) => { draft.dirty[key] = false; });
  controlDraft(updated);
}

function updateRange(input) {
  const min = Number(input.min || 0);
  const max = Number(input.max || 100);
  const percent = ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty("--value", `${Math.max(0, Math.min(100, percent))}%`);
}

function boundedRangeValue(range, value) {
  const minimum = Number(range.min || 0);
  const maximum = Number(range.max || 100);
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return Number(range.value);
  return Math.round(Math.max(minimum, Math.min(maximum, numeric)));
}

function normalizeJogDegrees(input) {
  const numeric = Number(input.value);
  const bounded = Math.max(0.1, Math.min(180, Number.isFinite(numeric) ? numeric : 5));
  const normalized = Math.round(bounded * 10) / 10;
  input.value = String(normalized);
  return normalized;
}

function installRangeKeypoints() {
  $$("input.range").forEach((input) => {
    if (input.closest(".range-keypoint-field")) return;
    const wrapper = document.createElement("div");
    wrapper.className = "range-keypoint-field";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const markers = document.createElement("div");
    markers.className = "range-keypoints";
    markers.setAttribute("aria-label", "Slider keypoints");
    [0, 0.25, 0.5, 0.75, 1].forEach((fraction) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${fraction * 100}%`;
      button.dataset.rangeTarget = input.id;
      button.dataset.rangeFraction = String(fraction);
      button.title = `Set ${input.id || "slider"} to ${fraction * 100}%`;
      button.setAttribute("aria-label", button.title);
      button.addEventListener("click", () => {
        const minimum = Number(input.min || 0);
        const maximum = Number(input.max || 100);
        input.value = Math.round(minimum + (maximum - minimum) * fraction);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      markers.appendChild(button);
    });
    wrapper.appendChild(markers);
  });
}

function updateCurrentPeaks() {
  if (!state?.connected) return;
  (state.motors || []).forEach((motor) => {
    const live = Math.max(0, Number(motor.current_a || 0));
    currentPeaks.set(motor.id, Math.max(currentPeaks.get(motor.id) || 0, live));
  });
}

function renderTorqueLimitOutput(rawLimit, motor = selectedMotor()) {
  const raw = Math.round(Number(rawLimit) || 0);
  const live = Math.max(0, Number(motor?.current_a || 0));
  const peak = currentPeaks.get(motor?.id) || live;
  setInputValue(el.torqueRawOutput, raw);
  el.torqueCurrentOutput.textContent = `LIVE ${live.toFixed(3)} A`;
  el.torquePeakCurrentOutput.textContent = `PEAK ${peak.toFixed(3)} A`;
  el.torqueOutput.title = "Torque Limit is an output cap, not a current command. Live and peak values come from measured Present_Current telemetry and depend on the actual load.";
}

function renderPorts() {
  const current = el.portSelect.value;
  const idStationCurrent = el.idStationPortSelect.value;
  if (!ports.length) {
    el.portSelect.innerHTML = '<option value="">No USB serial adapter detected</option>';
    el.idStationPortSelect.innerHTML = '<option value="">No USB serial adapter detected</option>';
    return;
  }
  const options = ports.map((port) => `<option value="${escapeHtml(port.device)}">${escapeHtml(port.label)} — ${escapeHtml(port.device)}</option>`).join("");
  el.portSelect.innerHTML = options;
  el.idStationPortSelect.innerHTML = options;
  if (ports.some((port) => port.device === current)) el.portSelect.value = current;
  if (ports.some((port) => port.device === idStationCurrent)) el.idStationPortSelect.value = idStationCurrent;
}

function renderConnection() {
  const connected = Boolean(state?.connected);
  const stationActive = Boolean(state?.id_station?.active);
  el.connectionBadge.classList.toggle("online", connected && state.mode === "hardware");
  el.connectionBadge.classList.toggle("simulation", connected && state.mode === "simulation");
  el.connectionBadge.classList.toggle("offline", !connected);
  el.connectionText.textContent = connected ? (state.mode === "simulation" ? "SIMULATION" : "BUS ONLINE") : "OFFLINE";
  el.connectButton.textContent = connected ? "DISCONNECT BUS" : stationActive ? "ID STATION ACTIVE" : "CONNECT BUS";
  el.connectButton.disabled = stationActive;
  el.scanButton.disabled = connected || stationActive || !el.portSelect.value;
  el.portSelect.disabled = connected || stationActive;
  el.baudSelect.disabled = connected || stationActive;
  el.recordButton.disabled = !connected;
  const recording = Boolean(state?.recording?.active);
  el.recordButton.classList.toggle("recording", recording);
  el.recordText.textContent = recording ? "Stop recording" : "Record run";
}

function renderMotors() {
  const motors = state?.motors || [];
  el.motorCount.textContent = motors.length;
  el.busOverviewButton.classList.toggle("active", activeView === "bus" && Boolean(state?.connected));
  el.idStationNavButton.classList.toggle("active", activeView === "id-station");
  el.busOverviewButton.disabled = !state?.connected;
  const signature = JSON.stringify({
    view: activeView,
    selected: state?.selected_id,
    motors: motors.map((motor) => [motor.id, motor.model, motor.online, motor.torque_enabled])
  });
  if (signature === lastMotorListSignature) return;
  lastMotorListSignature = signature;
  if (!motors.length) {
    el.motorList.innerHTML = '<div class="empty-list"><span>—</span><p>Connect a bus to discover motors</p></div>';
    return;
  }
  el.motorList.innerHTML = motors.map((motor) => `
    <button class="motor-item ${activeView === "motor" && motor.selected ? "active" : ""}" data-motor-id="${motor.id}" type="button">
      <span class="motor-id">${String(motor.id).padStart(2, "0")}</span>
      <span class="motor-copy"><strong>${escapeHtml(motor.model)}</strong><small>${escapeHtml(motor.variant)}</small></span>
      <span class="motor-state"><i style="background:${motor.online ? "var(--green)" : "var(--danger)"}"></i><small>${motor.torque_enabled ? "ARMED" : "FREE"}</small></span>
    </button>`).join("");
  $$(".motor-item").forEach((button) => button.addEventListener("click", () => selectMotor(Number(button.dataset.motorId))));
}

function renderIdStationMode() {
  const sequential = el.idModeSequential.checked;
  el.idSingleFields.classList.toggle("hidden", sequential);
  el.idSequentialFields.classList.toggle("hidden", !sequential);
}

function renderIdStation() {
  const station = state?.id_station || {};
  const active = Boolean(station.active);
  const connected = Boolean(state?.connected);
  const config = station.config;
  if (active && config) {
    el.idModeSingle.checked = config.mode === "single";
    el.idModeSequential.checked = config.mode === "sequential";
    el.idStationPortSelect.value = config.port;
    el.idStationBaudSelect.value = String(config.baudrate);
    el.idStationModelSelect.value = config.model;
    el.idSingleTarget.value = config.target_id;
    el.idSequenceStart.value = config.start_id;
    el.idSequenceEnd.value = config.end_id;
    el.idSequenceLoop.checked = Boolean(config.loop);
  }
  renderIdStationMode();

  const phase = station.phase || "idle";
  const phaseLabels = {
    idle: "IDLE", waiting: "WAITING FOR MOTOR", confirming: "CONFIRMING MOTOR",
    programming: "PROGRAMMING EEPROM", wait_unplug: "WAITING FOR UNPLUG",
    complete: "SEQUENCE COMPLETE", stopping: "STOPPING", stopped: "STOPPED", error: "ERROR"
  };
  el.idStationStatusChip.className = `id-station-status-chip ${phase}`;
  el.idStationStatusChip.textContent = connected && !active ? "DISCONNECT BUS FIRST" : (phaseLabels[phase] || phase.toUpperCase());
  el.idStationPhaseLabel.textContent = phaseLabels[phase] || phase.toUpperCase();
  el.idStationTarget.textContent = station.target_id ?? "—";
  el.idStationCompleted.textContent = station.completed || 0;
  el.idStationMessage.textContent = connected && !active
    ? "Disconnect the normal servo bus before starting the ID station."
    : station.message || "Configure a session, then start the ID station.";

  const detected = station.detected;
  if (detected) {
    const motorId = detected.id ?? detected.to_id;
    const model = String(detected.model || "supported motor").toUpperCase();
    const progress = detected.stable_scans != null
      ? `CONFIRMING ${detected.stable_scans}/${detected.required_scans}`
      : detected.from_id != null
        ? `VERIFIED ${detected.from_id} → ${detected.to_id}`
        : "DETECTED";
    el.idStationDetected.classList.remove("empty");
    el.idStationDetected.innerHTML = `<span class="id-detected-icon">${String(motorId).padStart(2, "0")}</span><div><strong>${escapeHtml(model)} · ID ${escapeHtml(motorId)}</strong><small>${escapeHtml(progress)} · ${Number(detected.baudrate || 0).toLocaleString()} BAUD</small></div>`;
  } else {
    el.idStationDetected.classList.add("empty");
    el.idStationDetected.innerHTML = `<span class="id-detected-icon">?</span><div><strong>NO MOTOR DETECTED</strong><small>${active ? "Keep exactly one supported motor connected" : "Waiting for a session"}</small></div>`;
  }

  const configControls = [
    el.idModeSingle, el.idModeSequential, el.idStationPortSelect, el.idStationBaudSelect,
    el.idStationModelSelect, el.idSingleTarget, el.idSequenceStart, el.idSequenceEnd, el.idSequenceLoop
  ];
  configControls.forEach((control) => { control.disabled = active; });
  el.idStationStartButton.disabled = active || connected || !el.idStationPortSelect.value;
  el.idStationStartButton.textContent = active ? "ID STATION RUNNING" : connected ? "DISCONNECT BUS TO START" : "START ID STATION";
  el.idStationStopButton.disabled = !active;
  el.idStationSimulationControls.classList.toggle("hidden", !station.simulation);
  el.idSimPlugButton.disabled = !active || Boolean(station.sim_motor_present);
  el.idSimUnplugButton.disabled = !active || !station.sim_motor_present;

  const events = station.events || [];
  const eventSignature = events.map((event) => event.number).join(",");
  if (eventSignature !== idStationEventSignature) {
    idStationEventSignature = eventSignature;
    el.idStationEventLog.innerHTML = events.length
      ? [...events].reverse().map((event) => {
          const time = new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          return `<div class="id-log-row ${escapeHtml(event.level)}"><b>#${String(event.number).padStart(2, "0")}</b><time>${escapeHtml(time)}</time><p>${escapeHtml(event.message)}</p></div>`;
        }).join("")
      : '<div class="id-log-empty">No ID-station events yet.</div>';
  }
}

function motorFaults(motor) {
  const labels = motor.status_labels || [];
  if (!motor.online) return ["COMMS LOST", ...labels];
  return labels;
}

function renderGroupControls(motors) {
  const anyArmed = motors.some((motor) => motor.torque_enabled);
  const allArmed = motors.length > 0 && motors.every((motor) => motor.torque_enabled);
  const allOnline = motors.length > 0 && motors.every((motor) => motor.online);
  const allAngleMode = motors.length > 0 && motors.every((motor) => motor.operating_mode === 0);
  const groupReady = allOnline && allAngleMode && allArmed;
  const shouldRelease = allArmed || (anyArmed && !allAngleMode);
  el.groupTorqueButton.classList.toggle("armed", anyArmed);
  el.groupTorqueButton.dataset.action = shouldRelease ? "release" : "arm";
  el.groupTorqueButtonText.textContent = shouldRelease ? "RELEASE ALL MOTORS" : anyArmed ? "ARM REMAINING MOTORS" : "ARM ALL MOTORS";
  el.groupTorqueButton.disabled = !motors.length;
  el.groupCenterEncoderButton.disabled = !allOnline || !allAngleMode || anyArmed || centerCalibrationPending;
  el.groupCenterEncoderButton.textContent = centerCalibrationPending ? "VERIFYING CENTERS…" : "SET ALL HERE = 2048";
  el.groupCenterEncoderButton.title = anyArmed
    ? "Release torque on every motor before encoder centering"
    : !allAngleMode
      ? "Every motor must be in angle-servo mode"
      : "Persistently make every motor's current shaft position read 2048";
  el.groupCommandButton.disabled = !groupReady;
  el.groupJogMinusButton.disabled = !groupReady || groupJogPending;
  el.groupJogPlusButton.disabled = !groupReady || groupJogPending;
  el.groupJogStepSelect.disabled = !motors.length || groupJogPending;
  el.groupJogHint.textContent = groupJogPending
    ? "Sending the preflighted relative targets across the bus…"
    : groupReady
      ? `Each press shifts every motor ${el.groupJogStepSelect.value}° from its own latest target.`
      : "Arm every online motor in angle mode to enable full-chain jogging.";
  el.groupControlStatus.textContent = !allOnline
    ? "OFFLINE MOTOR PRESENT"
    : !allAngleMode
      ? "MODE 0 REQUIRED"
      : allArmed
        ? "ALL MOTORS ARMED"
        : anyArmed
          ? `${motors.filter((motor) => motor.torque_enabled).length}/${motors.length} ARMED`
          : "TORQUE RELEASED";
  el.groupControlStatus.classList.toggle("ready", allOnline && allAngleMode && allArmed);
  setInputValue(el.groupTargetInput, el.groupTargetSlider.value);
  setInputValue(el.groupVelocityOutput, el.groupVelocitySlider.value);
  setInputValue(el.groupAccelerationOutput, el.groupAccelerationSlider.value);
  setInputValue(el.groupTorqueOutput, el.groupTorqueSlider.value);
  el.groupTorqueOutput.title = "Output cap. The Total Current card above is measured bus current.";
  [el.groupTargetSlider, el.groupVelocitySlider, el.groupAccelerationSlider, el.groupTorqueSlider].forEach(updateRange);
}

function renderGroupConfigEditor(motors) {
  const busSignature = motors.map((motor) => `${motor.id}:${motor.model_number}`).join("|");
  if (busSignature !== lastGroupConfigBusSignature) {
    groupConfigDirty.clear();
    lastGroupConfigBusSignature = busSignature;
  }

  GROUP_CONFIG_FIELDS.forEach((field) => {
    const input = el[field.element];
    if (!groupConfigDirty.has(field.key)) {
      const values = motors.map((motor) => motor.config?.[field.key]).filter((value) => value != null);
      const common = values.length === motors.length && new Set(values.map(Number)).size === 1;
      if (document.activeElement !== input) input.value = common ? String(values[0]) : "";
      input.placeholder = motors.length && !common ? "MIXED" : "";
    }
    input.classList.toggle("dirty", groupConfigDirty.has(field.key));
    input.disabled = groupConfigPending || !motors.length;
  });

  const offline = motors.filter((motor) => !motor.online);
  const armed = motors.filter((motor) => motor.torque_enabled);
  const ready = motors.length > 0 && !offline.length && !armed.length;
  el.groupConfigState.textContent = groupConfigPending
    ? "WRITING & VERIFYING"
    : offline.length
      ? `OFFLINE ID ${offline.map((motor) => motor.id).join(", ")}`
      : armed.length
        ? `RELEASE ${armed.length} ARMED`
        : ready
          ? "READY · TORQUE OFF"
          : "NO MOTORS";
  el.groupConfigState.classList.toggle("ready", ready && !groupConfigPending);
  el.groupConfigState.classList.toggle("danger", Boolean(offline.length || armed.length));

  const staged = GROUP_CONFIG_FIELDS.filter((field) => groupConfigDirty.has(field.key));
  el.groupConfigDirtySummary.textContent = staged.length
    ? `${staged.length} staged field${staged.length === 1 ? "" : "s"}: ${staged.map((field) => field.label).join(", ")}`
    : motors.length
      ? "Matching values are shown; MIXED means motors currently differ. Only edited fields are written."
      : "Connect a bus to edit persistent settings.";
  el.groupWriteConfigButton.disabled = !ready || !staged.length || groupConfigPending;
  el.groupWriteConfigButton.textContent = groupConfigPending
    ? "WRITING & VERIFYING…"
    : `VERIFY & WRITE TO ALL${motors.length ? ` ${motors.length}` : ""}`;
  el.groupWriteConfigButton.title = armed.length
    ? "Release torque on every motor before writing EEPROM"
    : offline.length
      ? "Every motor must be online"
      : staged.length
        ? "Preflight every motor, then write and read back the staged fields"
        : "Edit at least one field first";
}

function renderBusDashboard() {
  const motors = state?.motors || [];
  if (!motors.length) return;

  const online = motors.filter((motor) => motor.online).length;
  const armed = motors.filter((motor) => motor.torque_enabled).length;
  const moving = motors.filter((motor) => motor.moving).length;
  const totalCurrent = motors.reduce((sum, motor) => sum + Number(motor.current_a || 0), 0);
  const knownTorque = motors.filter((motor) => motor.estimated_torque_nm != null);
  const totalTorque = knownTorque.reduce((sum, motor) => sum + Number(motor.estimated_torque_nm || 0), 0);
  const peakTemp = Math.max(...motors.map((motor) => Number(motor.temperature || 0)));
  const voltages = motors.map((motor) => Number(motor.voltage || 0));
  const faults = motors.reduce((sum, motor) => sum + motorFaults(motor).length, 0);
  const commErrors = motors.reduce((sum, motor) => sum + Number(motor.communication_errors || 0), 0);
  renderGroupControls(motors);
  renderGroupConfigEditor(motors);

  el.busOnlineCount.textContent = `${online} / ${motors.length}`;
  el.busLinkSummary.textContent = state.mode === "simulation" ? "SIMULATED CHAIN" : `${Number(state.baudrate || 0).toLocaleString()} BAUD`;
  el.busArmedCount.textContent = armed;
  el.busMovingCount.textContent = `${moving} MOVING`;
  el.busTotalCurrent.textContent = `${totalCurrent.toFixed(3)} A`;
  el.busTotalTorque.textContent = knownTorque.length
    ? `≈ ${totalTorque.toFixed(2)} N·m EST. · ${knownTorque.length}/${motors.length} MOTORS`
    : "TORQUE ESTIMATE UNAVAILABLE";
  el.busPeakTemp.textContent = `${peakTemp}°C`;
  el.busVoltageRange.textContent = `${Math.min(...voltages).toFixed(1)}–${Math.max(...voltages).toFixed(1)} V BUS RANGE`;
  el.busFaultCount.textContent = faults;
  el.busCommCount.textContent = `${commErrors} COMMS ERRORS`;
  el.busChainMeta.textContent = `${motors.length} MOTOR${motors.length === 1 ? "" : "S"} · ${escapeHtml(state.port || "BUS")}`;
  el.busUpdatedAt.textContent = state.server_time
    ? `UPDATED ${new Date(state.server_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
    : "LIVE";

  el.busChainNodes.innerHTML = `
    <div class="chain-adapter"><span class="chain-node-id">USB</span><strong>WAVESHARE</strong><small>BUS ADAPTER</small></div>
    ${motors.map((motor) => {
      const motorFault = motorFaults(motor).length > 0;
      const classes = ["chain-node", motorFault ? "fault" : "", motor.torque_enabled ? "armed" : ""].filter(Boolean).join(" ");
      return `<span class="chain-link" aria-hidden="true"></span><button class="${classes}" data-open-motor="${motor.id}" type="button"><span class="chain-node-id">${String(motor.id).padStart(2, "0")}</span><strong>${escapeHtml(motor.model)}</strong><small>${motorFault ? escapeHtml(motorFaults(motor).join(" / ")) : motor.torque_enabled ? "ARMED" : "ONLINE · FREE"}</small></button>`;
    }).join("")}`;

  el.busMotorCards.innerHTML = motors.map((motor) => {
    const status = motorFaults(motor);
    const torque = motor.estimated_torque_nm == null ? "—" : `${Number(motor.estimated_torque_nm).toFixed(2)} N·m`;
    const statusText = status.length ? status.join(" / ") : "CLEAR";
    const commErrors = Number(motor.communication_errors || 0);
    return `<button class="bus-motor-card" data-open-motor="${motor.id}" type="button">
      <span class="bus-motor-card-head">
        <span class="bus-motor-card-id">${String(motor.id).padStart(2, "0")}</span>
        <span class="bus-motor-card-name"><strong>${escapeHtml(motor.model)}</strong><small>${escapeHtml(motor.variant)}</small></span>
        <span class="motor-health-pill ${status.length ? "fault" : ""}">${status.length ? "CHECK" : "HEALTHY"}</span>
      </span>
      <span class="bus-motor-card-stats">
        <span class="bus-motor-stat"><span>POSITION</span><strong>${Number(motor.degrees).toFixed(1)}°</strong><small>${motor.position} RAW</small></span>
        <span class="bus-motor-stat"><span>VELOCITY</span><strong>${signed(motor.velocity_raw)}</strong><small>RAW</small></span>
        <span class="bus-motor-stat"><span>LOAD</span><strong>${Number(motor.load_percent).toFixed(1)}%</strong><small>${Math.abs(Number(motor.load_raw || 0))} RAW</small></span>
        <span class="bus-motor-stat"><span>CURRENT</span><strong>${Number(motor.current_a || 0).toFixed(3)} A</strong><small>${Number(motor.current_raw || 0)} RAW</small></span>
        <span class="bus-motor-stat"><span>EST. TORQUE</span><strong>${torque}</strong><small>CURRENT-BASED</small></span>
        <span class="bus-motor-stat"><span>VOLTAGE</span><strong>${Number(motor.voltage).toFixed(1)} V</strong><small>INPUT</small></span>
        <span class="bus-motor-stat"><span>TEMPERATURE</span><strong>${motor.temperature}°C</strong><small>DEVICE</small></span>
        <span class="bus-motor-stat mode"><span>OPERATING MODE</span><strong>MODE ${motor.operating_mode}</strong><small>${escapeHtml(motor.operating_mode_name)}</small></span>
        <span class="bus-motor-stat status ${status.length ? "status-fault" : "status-clear"}"><span>STATUS</span><strong>${escapeHtml(statusText)}</strong><small>${commErrors ? `${commErrors} COMMS ERROR${commErrors === 1 ? "" : "S"}` : "NO COMMS ERRORS"}</small></span>
      </span>
      <span class="bus-motor-card-foot"><span>${motor.torque_enabled ? "ARMED" : "TORQUE FREE"} · ${motor.online ? "ONLINE" : "COMMS LOST"}</span><strong>OPEN CONTROL →</strong></span>
    </button>`;
  }).join("");

  $$('[data-open-motor]').forEach((node) => {
    node.addEventListener("click", () => selectMotor(Number(node.dataset.openMotor)));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectMotor(Number(node.dataset.openMotor));
      }
    });
  });
}

function renderModeControl(motor, draft) {
  const actual = CONTROL_MODES[motor.operating_mode] || CONTROL_MODES[0];
  const requested = CONTROL_MODES[draft.mode] || actual;
  setInputValue(el.modeSelect, draft.mode);
  el.modeHelp.textContent = draft.mode !== motor.operating_mode
    ? `${requested.help} Press Switch to write and verify this mode.`
    : actual.help;
  el.applyModeButton.disabled = motor.torque_enabled || draft.mode === motor.operating_mode;
  el.modeSelect.disabled = motor.torque_enabled;
  el.modeSelect.closest(".mode-control").classList.toggle("mode-warning", motor.operating_mode !== 0);

  [el.targetInput, el.targetSlider].forEach((input) => {
    input.min = actual.min;
    input.max = actual.max;
  });
  el.motionCommandLabel.textContent = actual.label;
  el.targetInputUnit.textContent = actual.unit;
  el.commandMinLabel.textContent = actual.minLabel;
  el.commandMaxLabel.textContent = actual.maxLabel;
  el.centerButton.textContent = actual.neutralLabel;
  el.goButtonText.textContent = actual.action;
  el.velocityRow.classList.toggle("hidden", motor.operating_mode === 1 || motor.operating_mode === 2);
}

function renderDashboard() {
  const motor = selectedMotor();
  if (!motor) return;

  if (selectedHistoryId !== motor.id) {
    selectedHistoryId = motor.id;
    history.length = 0;
  }
  const traceTarget = motor.operating_mode === 0 || motor.operating_mode === 3
    ? motor.target_position : motor.position;
  history.push({ position: motor.position, target: traceTarget, load: motor.load_percent });
  if (history.length > MAX_HISTORY) history.shift();

  el.headerModel.textContent = motor.model;
  el.headerId.textContent = `ID ${String(motor.id).padStart(2, "0")}`;
  el.headerVariant.textContent = motor.variant;
  el.headerStatus.textContent = motor.online ? "ONLINE" : "COMMS LOST";
  el.headerStatus.style.color = motor.online ? "var(--green)" : "var(--danger)";
  if (motor.datasheet) {
    el.datasheetLink.href = motor.datasheet;
    el.datasheetLink.classList.remove("hidden");
  } else {
    el.datasheetLink.classList.add("hidden");
  }

  const angle = Math.max(0, Math.min(360, motor.degrees));
  el.positionDial.style.setProperty("--dial-angle", `${angle}deg`);
  el.positionDegrees.textContent = `${motor.degrees.toFixed(1)}°`;
  el.positionRaw.textContent = `${motor.position} RAW`;
  const mode = CONTROL_MODES[motor.operating_mode];
  const liveCommand = motor.operating_mode === 0 ? motor.target_position : motor.mode_command;
  el.targetReadoutLabel.textContent = mode?.readout || "COMMAND";
  el.targetReadout.textContent = liveCommand;
  el.targetReadoutUnit.textContent = mode?.unit || "RAW";
  const error = motor.target_position - motor.position;
  el.errorReadout.textContent = motor.operating_mode === 0 || motor.operating_mode === 3
    ? `${error > 0 ? "+" : ""}${error}` : "—";
  el.motionReadout.textContent = motor.moving ? "MOVING" : (motor.torque_enabled ? "HOLD" : "FREE");
  el.modeReadout.textContent = `MODE ${motor.operating_mode}`;

  el.velocityValue.textContent = signed(motor.velocity_raw);
  el.rpmValue.textContent = `~${signed(motor.velocity_rpm_estimate.toFixed(1))} RPM`;
  el.loadValue.textContent = `${Number(motor.load_percent).toFixed(1)}%`;
  el.loadRaw.textContent = `${Math.abs(Number(motor.load_raw))} RAW`;
  el.voltageValue.textContent = `${motor.voltage.toFixed(1)} V`;
  el.voltageState.textContent = motor.voltage >= 9 && motor.voltage <= 12.8 ? "NOMINAL" : "CHECK SUPPLY";
  el.temperatureValue.textContent = `${motor.temperature}°C`;
  el.temperatureState.textContent = motor.temperature < 55 ? "NOMINAL" : (motor.temperature < 70 ? "WARM" : "PROTECTION");
  el.currentValue.textContent = `${Number(motor.current_a || 0).toFixed(3)} A`;
  el.currentRaw.textContent = `${motor.current_raw} RAW · ${(Number(motor.current_scale_a_per_lsb || .0065) * 1000).toFixed(1)} mA/LSB`;
  el.torqueEstimate.textContent = motor.estimated_torque_nm == null
    ? "ESTIMATE UNAVAILABLE · Kt NEEDED"
    : `≈ ${Number(motor.estimated_torque_nm).toFixed(2)} N·m · ${Number(motor.estimated_torque_kgcm).toFixed(1)} kgf·cm`;
  el.deviceStatus.textContent = motor.status_labels?.length ? motor.status_labels.join(" / ") : "CLEAR";
  el.commErrors.textContent = `${motor.communication_errors} COMMS ERRORS`;
  el.sampleStatus.textContent = motor.last_seen ? "STREAMING" : "WAITING";

  el.torqueButton.classList.toggle("armed", motor.torque_enabled);
  el.torqueButton.querySelector("span:last-child").textContent = motor.torque_enabled ? "RELEASE TORQUE" : "ARM TORQUE";
  el.centerEncoderButton.disabled = !motor.online || motor.torque_enabled || motor.operating_mode !== 0 || centerCalibrationPending;
  el.centerEncoderButton.textContent = centerCalibrationPending ? "VERIFYING…" : "SET HERE = 2048";
  el.centerEncoderButton.title = motor.torque_enabled
    ? "Release torque before encoder centering"
    : motor.operating_mode !== 0
      ? "Switch to angle-servo mode before encoder centering"
      : "Persistently make this shaft position read 2048";
  el.torqueHint.classList.toggle("armed", motor.torque_enabled);
  el.torqueHint.lastChild.textContent = motor.torque_enabled
    ? `Servo is armed in ${mode?.name || "unknown"} mode. Commands can move hardware immediately.`
    : `Torque is released. Mode ${motor.operating_mode} will be seeded with a zero-motion command when armed.`;
  el.goButton.disabled = !motor.torque_enabled || !mode;

  const draft = controlDraft(motor);
  renderModeControl(motor, draft);
  const canJog = motor.online && motor.torque_enabled && motor.operating_mode === 0 && !jogPending;
  el.jogMinusButton.disabled = !canJog;
  el.jogPlusButton.disabled = !canJog;
  el.jogHint.textContent = jogPending
    ? "Jog command in progress…"
    : motor.operating_mode !== 0
      ? "Switch to angle-servo mode before jogging."
      : motor.torque_enabled
        ? `Jogging is armed. Each press moves ${el.jogStepSelect.value}° from the current target.`
        : "Arm torque in angle-servo mode to jog from the current target.";
  setInputValue(el.targetInput, draft.target);
  setInputValue(el.targetSlider, draft.target);
  setInputValue(el.velocitySlider, draft.velocity);
  setInputValue(el.accelerationSlider, draft.acceleration);
  setInputValue(el.torqueSlider, draft.torque);
  setInputValue(el.velocityOutput, el.velocitySlider.value);
  setInputValue(el.accelerationOutput, el.accelerationSlider.value);
  renderTorqueLimitOutput(el.torqueSlider.value, motor);
  const ratedLimit = motor.rated_torque_limit_raw;
  if (ratedLimit == null || motor.rated_torque_kgcm == null) {
    el.torqueRatedMarker.classList.add("hidden");
  } else {
    el.torqueRatedMarker.classList.remove("hidden");
    el.torqueRatedMarker.style.left = `${Number(ratedLimit) / 10}%`;
    el.torqueRatedLabel.textContent = `RATED ${Number(motor.rated_torque_kgcm).toFixed(0)} kgf·cm · ${ratedLimit}`;
    el.torqueRatedMarker.title = `Stage ${ratedLimit} raw, the nominal ${motor.rated_torque_kgcm} kgf·cm rated-torque point for this ${motor.model}`;
  }
  [el.targetSlider, el.velocitySlider, el.accelerationSlider, el.torqueSlider].forEach(updateRange);

  const config = motor.config || {};
  setInputValue(el.configP, config.p_gain);
  setInputValue(el.configD, config.d_gain);
  setInputValue(el.configI, config.i_gain);
  setInputValue(el.configMinPosition, config.min_position);
  setInputValue(el.configMaxPosition, config.max_position);
  setInputValue(el.configMaxTemp, config.max_temperature);
  setInputValue(el.configMinVoltage, config.min_voltage);
  setInputValue(el.configMaxVoltage, config.max_voltage);
  el.writeConfigButton.disabled = motor.torque_enabled;

  el.specModel.textContent = `${motor.model} / #${motor.model_number}`;
  el.specTorque.textContent = motor.stall_torque_kgcm ? `${motor.stall_torque_kgcm} kg·cm` : "Verify sheet";
  el.specCurrent.textContent = motor.stall_current_a ? `${motor.stall_current_a} A` : "Verify sheet";
  el.specVoltage.textContent = motor.operating_voltage;
  el.specTemp.textContent = motor.operating_temp;
  renderCaptures();
  drawChart();
}

function renderCaptures() {
  const captures = state?.captures || [];
  const canRecall = selectedMotor()?.operating_mode === 0;
  if (!captures.length) {
    el.captureList.innerHTML = '<div class="capture-empty"><div class="crosshair"></div><strong>NO POSITIONS YET</strong><p>Move the servo by hand with torque released, then capture its live position.</p></div>';
    return;
  }
  el.captureList.innerHTML = captures.map((capture) => `
    <div class="capture-item">
      <div class="capture-info"><strong>${escapeHtml(capture.name)}</strong><div><span>${capture.position} RAW</span><span>${Number(capture.degrees).toFixed(1)}°</span></div></div>
      <div class="capture-actions"><button class="capture-go" data-capture-go="${escapeHtml(capture.capture_id)}" type="button" ${canRecall ? "" : "disabled title=\"Switch to angle-servo mode to recall positions\""}>GO HERE</button><button class="capture-delete" data-capture-delete="${escapeHtml(capture.capture_id)}" type="button" aria-label="Delete capture">×</button></div>
    </div>`).join("");
  $$('[data-capture-go]').forEach((button) => button.addEventListener("click", () => goToCapture(button.dataset.captureGo)));
  $$('[data-capture-delete]').forEach((button) => button.addEventListener("click", () => deleteCapture(button.dataset.captureDelete)));
}

function render() {
  updateCurrentPeaks();
  renderConnection();
  renderMotors();
  renderIdStation();
  const connected = Boolean(state?.connected);
  const motor = selectedMotor();
  if (!connected && activeView !== "id-station") activeView = "bus";
  if (activeView === "motor" && !motor) activeView = "bus";
  el.welcomeState.classList.toggle("hidden", connected || activeView === "id-station");
  el.idStationView.classList.toggle("hidden", activeView !== "id-station");
  el.busDashboard.classList.toggle("hidden", !connected || activeView !== "bus");
  el.dashboard.classList.toggle("hidden", !connected || activeView !== "motor" || !motor);
  if (connected && activeView === "bus") {
    const now = performance.now();
    const interacting = el.busDashboard.querySelector("[data-open-motor]:hover, [data-open-motor]:focus");
    if (!interacting && (now - lastBusRenderAt >= 140 || !el.busMotorCards.children.length)) {
      renderBusDashboard();
      lastBusRenderAt = now;
    }
  } else if (connected && activeView === "motor" && motor) {
    renderDashboard();
  }
  if (state?.message && state.message !== lastMessage) {
    lastMessage = state.message;
    toast(state.message, state.message_level || "success");
  }
}

function drawChart() {
  const canvas = el.traceChart;
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = bounds.width * dpr;
  canvas.height = bounds.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = bounds.width;
  const height = bounds.height;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--chart-grid").trim();
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = (height - 1) * i / 4 + .5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
  }
  for (let i = 0; i <= 8; i++) {
    const x = (width - 1) * i / 8 + .5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }
  drawSeries(ctx, width, height, history.map((v) => v.position), 0, 4095, "#9dc53a", 1.7);
  drawSeries(ctx, width, height, history.map((v) => v.target), 0, 4095, "#4c78d0", 1, [4, 4]);
  drawSeries(ctx, width, height, history.map((v) => v.load), 0, 100, "#f18a43", 1.1);
}

function drawSeries(ctx, width, height, values, min, max, color, lineWidth, dash = []) {
  if (values.length < 2) return;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = index * width / Math.max(MAX_HISTORY - 1, values.length - 1);
    const y = height - ((Math.max(min, Math.min(max, value)) - min) / (max - min)) * height;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dash);
  ctx.stroke();
  ctx.setLineDash([]);
}

async function refreshPorts() {
  try {
    ports = await api("/api/ports");
    renderPorts();
    renderConnection();
    if (!ports.length) toast("No serial adapter detected. Connect a compatible adapter and refresh.", "warning");
  } catch (error) { handleError(error); }
}

async function scanBus() {
  if (!el.portSelect.value) return toast("Select a serial adapter first", "warning");
  setBusy(true, "SCANNING — READ ONLY");
  try {
    const found = await api("/api/scan", { method: "POST", body: connectionPayload() });
    if (!found.length) toast("No servos responded at this baud rate", "warning");
    else toast(`Found ${found.length} servo${found.length === 1 ? "" : "s"}: ${found.map((m) => `ID ${m.id} ${m.model}`).join(", ")}`);
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

async function toggleConnection() {
  setBusy(true, state?.connected ? "DISCONNECTING SAFELY" : "CONNECTING — TORQUE OFF");
  try {
    state = state?.connected
      ? await api("/api/disconnect", { method: "POST" })
      : await api("/api/connect", { method: "POST", body: connectionPayload() });
    history.length = 0;
    controlDrafts.clear();
    groupConfigDirty.clear();
    lastGroupConfigBusSignature = "";
    currentPeaks.clear();
    activeView = "bus";
    lastBusRenderAt = 0;
    window.history.replaceState(null, "", "#bus");
    render();
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

function connectionPayload() {
  return { port: el.portSelect.value, baudrate: Number(el.baudSelect.value) };
}

function showBusDashboard(updateRoute = true) {
  activeView = "bus";
  lastBusRenderAt = 0;
  if (updateRoute && location.hash !== "#bus") window.history.pushState(null, "", "#bus");
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showIdStation(updateRoute = true) {
  activeView = "id-station";
  if (updateRoute && location.hash !== "#id-station") {
    window.history.pushState(null, "", "#id-station");
  }
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function selectMotor(motorId, updateRoute = true) {
  try {
    state = await api(`/api/select/${motorId}`, { method: "POST" });
    activeView = "motor";
    selectedHistoryId = null;
    if (updateRoute && location.hash !== `#motor/${motorId}`) {
      window.history.pushState(null, "", `#motor/${motorId}`);
    }
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) { handleError(error); }
}

async function applyRoute() {
  if (location.hash === "#id-station") {
    showIdStation(false);
    return;
  }
  if (!state?.connected) {
    activeView = "bus";
    render();
    return;
  }
  const match = location.hash.match(/^#motor\/(\d+)$/);
  const motorId = match ? Number(match[1]) : null;
  if (motorId != null && state.motors.some((motor) => motor.id === motorId)) {
    if (state.selected_id !== motorId) await selectMotor(motorId, false);
    else {
      activeView = "motor";
      selectedHistoryId = null;
      render();
    }
  } else {
    showBusDashboard(false);
  }
}

async function toggleTorque() {
  const motor = selectedMotor();
  if (!motor) return;
  const mode = CONTROL_MODES[motor.operating_mode];
  if (!motor.torque_enabled && !(await requestConfirmation({
    key: "arm-torque",
    eyebrow: "MOTION SAFETY",
    title: `Arm ${motor.model} ID ${motor.id}?`,
    message: "Torque output will become active on this servo.",
    details: [
      `Operating mode: ${mode?.name || "Unknown"}`,
      "A zero-motion command is written before torque is enabled.",
      "Commands issued after arming can move hardware immediately."
    ],
    confirmLabel: "ARM TORQUE",
    skipLabel: "Don't ask again before arming torque"
  }))) return;
  try {
    await api(`/api/motors/${motor.id}/torque`, { method: "POST", body: { enabled: !motor.torque_enabled } });
  } catch (error) { handleError(error); }
}

async function toggleGroupTorque() {
  const motors = state?.motors || [];
  if (!motors.length) return;
  const release = el.groupTorqueButton.dataset.action === "release";
  if (!release && !(await requestConfirmation({
    key: "arm-all-motors",
    eyebrow: "GROUP MOTION SAFETY",
    title: `Arm all ${motors.length} motors?`,
    message: "Torque output will become active across the full connected chain.",
    details: [
      "Every motor must already be in angle-servo mode.",
      "Each motor is seeded at its own live position before torque is enabled.",
      "The group command can move all motors after arming."
    ],
    confirmLabel: "ARM ALL MOTORS",
    skipLabel: "Don't ask again before arming the full chain"
  }))) return;

  setBusy(true, release ? "RELEASING ALL MOTORS" : "ARMING ALL — ZERO MOTION");
  try {
    state = await api("/api/group/torque", { method: "POST", body: { enabled: !release } });
    lastBusRenderAt = 0;
    render();
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

async function commandAllMotors() {
  const motors = state?.motors || [];
  if (!motors.length || el.groupCommandButton.disabled) return;
  const position = Number(el.groupTargetSlider.value);
  const velocity = Number(el.groupVelocitySlider.value);
  const acceleration = Number(el.groupAccelerationSlider.value);
  const torqueLimit = Number(el.groupTorqueSlider.value);
  if (!(await requestConfirmation({
    key: "command-all-motors",
    eyebrow: "GROUP MOTION SAFETY",
    title: `Move ${motors.length} motors to ${position} raw?`,
    message: `Every armed motor will receive the same ${position} raw (${(position * 360 / 4095).toFixed(1)}°) target.`,
    details: [
      `Velocity ${velocity} · acceleration ${acceleration} · torque limit ${torqueLimit}`,
      "Every motor's configured travel range is validated first.",
      "Commands are transmitted sequentially over the half-duplex bus."
    ],
    confirmLabel: "COMMAND ALL MOTORS",
    skipLabel: "Don't ask again before shared position commands"
  }))) return;

  setBusy(true, `COMMANDING ${motors.length} MOTORS`);
  try {
    state = await api("/api/group/position", {
      method: "POST",
      body: { position, velocity, acceleration, torque_limit: torqueLimit }
    });
    lastBusRenderAt = 0;
    render();
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

async function jogAllMotors(direction) {
  const motors = state?.motors || [];
  if (!motors.length || groupJogPending) return;
  const ready = motors.every((motor) => motor.online && motor.torque_enabled && motor.operating_mode === 0);
  if (!ready) return toast("Arm every online motor in angle mode before jogging the full chain", "warning");

  const degrees = normalizeJogDegrees(el.groupJogStepSelect);
  const increment = Math.max(1, Math.round(degrees * 4095 / 360));
  const delta = direction * increment;
  const directionLabel = delta < 0 ? "backward" : "forward";
  if (!(await requestConfirmation({
    key: "jog-all-motors",
    eyebrow: "GROUP MOTION SAFETY",
    title: `Jog all ${motors.length} motors ${directionLabel}?`,
    message: `Each motor will move ${degrees}° from its own latest commanded target.`,
    details: [
      `Relative delta ${delta > 0 ? "+" : ""}${delta} raw · velocity ${el.groupVelocitySlider.value} · acceleration ${el.groupAccelerationSlider.value}`,
      "Every resulting target is checked against that motor's configured travel range before any position is sent.",
      "Goal positions are transmitted sequentially over the half-duplex bus."
    ],
    confirmLabel: `JOG ALL ${directionLabel.toUpperCase()}`,
    skipLabel: "Don't ask again before full-chain jogs"
  }))) return;

  groupJogPending = true;
  renderGroupControls(motors);
  try {
    state = await api("/api/group/jog", {
      method: "POST",
      body: {
        delta,
        velocity: Number(el.groupVelocitySlider.value),
        acceleration: Number(el.groupAccelerationSlider.value),
        torque_limit: Number(el.groupTorqueSlider.value)
      }
    });
    const targets = state.motors.map((motor) => Number(motor.target_position));
    if (new Set(targets).size === 1) {
      el.groupTargetSlider.value = targets[0];
      el.groupTargetInput.value = targets[0];
    }
    const minimum = Math.min(...targets);
    const maximum = Math.max(...targets);
    const targetSummary = minimum === maximum ? `${minimum}` : `${minimum}–${maximum}`;
    toast(`Jogged ${motors.length} motors ${delta < 0 ? "−" : "+"}${degrees.toFixed(1)}° · targets ${targetSummary} raw`);
    lastBusRenderAt = 0;
  } catch (error) { handleError(error); }
  finally {
    groupJogPending = false;
    render();
  }
}

async function centerMotorEncoder() {
  const motor = selectedMotor();
  if (!motor || centerCalibrationPending) return;
  if (motor.torque_enabled) return toast("Release torque before centering the encoder", "warning");
  if (motor.operating_mode !== 0) return toast("Switch to angle-servo mode before centering the encoder", "warning");
  const before = Number(motor.position);
  if (!(await requestConfirmation({
    key: "center-motor-encoder",
    eyebrow: "PERSISTENT ENCODER CALIBRATION",
    title: `Make this ${motor.model} position 2048?`,
    message: `ID ${motor.id}'s current physical shaft position (${before} raw) will become encoder value 2048.`,
    details: [
      "Torque must remain released; position the shaft by hand at the intended center first.",
      "This invokes Feetech's one-key midpoint calibration and persists after power is removed.",
      "Saved raw targets, captured positions, and the physical meaning of existing travel limits will shift.",
      "MotorLab forces torque off afterward and reads Present_Position back as exactly 2048."
    ],
    confirmLabel: "SET CURRENT POSITION TO 2048",
    skipLabel: "Don't ask again before centering one encoder",
    tone: "danger"
  }))) return;

  centerCalibrationPending = true;
  renderDashboard();
  try {
    const updated = await api(`/api/motors/${motor.id}/center-encoder`, {
      method: "POST",
      body: { confirmation: "CENTER_ENCODER_2048" }
    });
    controlDrafts.delete(motor.id);
    acceptMotorUpdate(updated, []);
    toast(`ID ${motor.id} encoder verified: ${before} → ${updated.position} raw · torque off`);
  } catch (error) { handleError(error); }
  finally {
    centerCalibrationPending = false;
    render();
  }
}

async function centerAllEncoders() {
  const motors = state?.motors || [];
  if (!motors.length || centerCalibrationPending) return;
  if (motors.some((motor) => motor.torque_enabled)) return toast("Release torque on every motor before bus centering", "warning");
  if (motors.some((motor) => motor.operating_mode !== 0)) return toast("Switch every motor to angle-servo mode before bus centering", "warning");
  if (!(await requestConfirmation({
    key: "center-bus-encoders",
    eyebrow: "PERSISTENT BUS CALIBRATION",
    title: `Make all ${motors.length} current positions 2048?`,
    message: "Each motor's current physical shaft position will become encoder value 2048.",
    details: [
      "Release all torque and place every shaft at its intended physical center before continuing.",
      "The complete bus is preflighted first, then Feetech's persistent midpoint command is sent sequentially.",
      "Saved raw targets, captured positions, and the physical meaning of existing travel limits will shift.",
      "Each motor is forced torque-off and read back at exactly 2048; a communication failure may leave earlier IDs already centered."
    ],
    confirmLabel: "SET ALL CURRENT POSITIONS TO 2048",
    skipLabel: "Don't ask again before centering the full bus",
    tone: "danger"
  }))) return;

  centerCalibrationPending = true;
  renderGroupControls(motors);
  try {
    state = await api("/api/group/center-encoders", {
      method: "POST",
      body: { confirmation: "CENTER_ENCODER_2048" }
    });
    controlDrafts.clear();
    el.groupTargetSlider.value = 2048;
    el.groupTargetInput.value = 2048;
    lastBusRenderAt = 0;
    toast(`Verified ${motors.length} encoder centers at 2048 · all torque off`);
  } catch (error) { handleError(error); }
  finally {
    centerCalibrationPending = false;
    render();
  }
}

function collectGroupPersistentValues() {
  const values = {};
  for (const field of GROUP_CONFIG_FIELDS) {
    if (!groupConfigDirty.has(field.key)) continue;
    const input = el[field.element];
    const value = Number(input.value);
    if (input.value === "" || !Number.isInteger(value)) {
      input.focus();
      throw new Error(`${field.label} must be a whole number`);
    }
    const minimum = Number(input.min);
    const maximum = Number(input.max);
    if (value < minimum || value > maximum) {
      input.focus();
      throw new Error(`${field.label} must be ${minimum}–${maximum}`);
    }
    values[field.key] = value;
  }
  return values;
}

async function writeAllConfig() {
  const motors = state?.motors || [];
  if (!motors.length || groupConfigPending || !groupConfigDirty.size) return;
  const offline = motors.filter((motor) => !motor.online);
  const armed = motors.filter((motor) => motor.torque_enabled);
  if (offline.length) return toast(`Bus EEPROM write blocked by offline ID(s): ${offline.map((motor) => motor.id).join(", ")}`, "warning");
  if (armed.length) return toast(`Release torque on ID(s) ${armed.map((motor) => motor.id).join(", ")} before writing EEPROM`, "warning");

  let values;
  try {
    values = collectGroupPersistentValues();
  } catch (error) {
    return handleError(error);
  }
  const staged = GROUP_CONFIG_FIELDS.filter((field) => Object.hasOwn(values, field.key));
  if (!(await requestConfirmation({
    key: "write-all-persistent-config",
    eyebrow: "PERSISTENT BUS EEPROM WRITE",
    title: `Write ${staged.length} setting${staged.length === 1 ? "" : "s"} to all ${motors.length} motors?`,
    message: "The staged values will remain on every servo after power is removed.",
    details: [
      `Staged: ${staged.map((field) => `${field.label} = ${values[field.key]}`).join(" · ")}`,
      "Every motor must be online with torque released; all ranges and paired limits are preflighted before the first write.",
      "Each register is written and read back one motor at a time. A communication failure can leave earlier verified motors changed."
    ],
    confirmLabel: `WRITE & VERIFY ALL ${motors.length}`,
    skipLabel: "Don't ask again before bus-wide EEPROM writes",
    tone: "danger"
  }))) return;

  groupConfigPending = true;
  renderGroupConfigEditor(motors);
  setBusy(true, `WRITING & VERIFYING ${motors.length} MOTORS`);
  try {
    state = await api("/api/group/persistent-config", {
      method: "POST",
      body: { confirmation: "WRITE_ALL_EEPROM", values }
    });
    groupConfigDirty.clear();
    lastBusRenderAt = 0;
    toast(`Verified ${staged.length} EEPROM setting${staged.length === 1 ? "" : "s"} on all ${motors.length} motors`);
  } catch (error) { handleError(error); }
  finally {
    groupConfigPending = false;
    setBusy(false);
    render();
  }
}

function readIdStationNumber(input, label) {
  const value = Number(input.value);
  if (!Number.isInteger(value) || value < 1 || value > 253) {
    input.focus();
    throw new Error(`${label} must be a whole number from 1 to 253`);
  }
  return value;
}

async function startIdStation() {
  if (state?.connected) return toast("Disconnect the normal servo bus before starting the ID station", "warning");
  const mode = el.idModeSequential.checked ? "sequential" : "single";
  let targetId, startId, endId;
  try {
    targetId = readIdStationNumber(el.idSingleTarget, "Target ID");
    startId = readIdStationNumber(el.idSequenceStart, "Start ID");
    endId = readIdStationNumber(el.idSequenceEnd, "End ID");
    if (mode === "sequential" && startId > endId) throw new Error("Start ID must be less than or equal to end ID");
  } catch (error) { return handleError(error); }
  const port = el.idStationPortSelect.value;
  if (!port) return toast("Select a serial adapter for the ID station", "warning");
  const loop = mode === "sequential" && el.idSequenceLoop.checked;
  const assignment = mode === "single"
    ? `Every newly plugged motor will be assigned ID ${targetId}.`
    : `Motors will be assigned IDs ${startId} through ${endId}${loop ? ", then wrap to the start" : ", then stop"}.`;
  if (!(await requestConfirmation({
    key: `start-id-station-${mode}-${targetId}-${startId}-${endId}-${loop}`,
    eyebrow: "AUTOMATIC PERSISTENT ID WRITES",
    title: "Start the plug-and-program ID station?",
    message: assignment,
    details: [
      "Keep exactly one motor physically connected to this adapter at a time.",
      "Two matching scans are required; multiple responders, unstable scans, unsupported models, and model mismatches are rejected without writing.",
      "LeRobot setup_motor disables torque, writes the ID and default 1,000,000 baud, then MotorLab verifies with a ping and final scan.",
      "After verification, no next motor is accepted until the programmed motor has been unplugged."
    ],
    confirmLabel: "START AUTOMATIC ID WRITES",
    skipLabel: "Don't ask again for this exact ID-station setup",
    tone: "danger"
  }))) return;

  setBusy(true, "STARTING GUARDED ID STATION");
  try {
    const station = await api("/api/id-station/start", {
      method: "POST",
      body: {
        mode, port, baudrate: Number(el.idStationBaudSelect.value),
        model: el.idStationModelSelect.value,
        target_id: targetId, start_id: startId, end_id: endId, loop,
        confirmation: "START_ID_STATION"
      }
    });
    state.id_station = station;
    idStationEventSignature = "";
    render();
    toast(`ID station started · next target ID ${station.target_id}`);
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

async function stopIdStation() {
  if (!state?.id_station?.active) return;
  try {
    const station = await api("/api/id-station/stop", { method: "POST" });
    state.id_station = station;
    render();
    toast("ID station stopped; no further motors will be programmed", "warning");
  } catch (error) { handleError(error); }
}

async function simulateIdStationMotor(present) {
  try {
    const station = await api("/api/id-station/simulate-motor", {
      method: "POST",
      body: { present, motor_id: 1, model_number: 777 }
    });
    state.id_station = station;
    render();
  } catch (error) { handleError(error); }
}

async function applyMode() {
  const motor = selectedMotor();
  if (!motor) return;
  const requested = Number(el.modeSelect.value);
  const mode = CONTROL_MODES[requested];
  if (!mode || requested === motor.operating_mode) return;
  if (motor.torque_enabled) return toast("Release torque before changing operating mode", "warning");
  if (!(await requestConfirmation({
    key: "change-operating-mode",
    eyebrow: "DEVICE CONFIGURATION",
    title: `Switch to ${mode.name}?`,
    message: `${motor.model} ID ${motor.id} will be changed to operating mode ${requested}.`,
    details: [
      "This writes the servo's persistent operating-mode register.",
      "The new value is read back and verified.",
      "Torque remains released after the change."
    ],
    confirmLabel: `SWITCH TO MODE ${requested}`,
    skipLabel: "Don't ask again before changing operating modes"
  }))) return;
  setBusy(true, `SWITCHING TO MODE ${requested}`);
  try {
    const updated = await api(`/api/motors/${motor.id}/mode`, {
      method: "POST", body: { mode: requested }
    });
    acceptMotorUpdate(updated, ["mode", "target"]);
    render();
  } catch (error) { handleError(error); }
  finally { setBusy(false); }
}

async function emergencyStop() {
  try {
    state = await api("/api/emergency-stop", { method: "POST" });
    render();
  } catch (error) { handleError(error); }
}

async function sendTarget() {
  const motor = selectedMotor();
  if (!motor) return;
  try {
    const common = {
      velocity: Number(el.velocitySlider.value), acceleration: Number(el.accelerationSlider.value),
      torque_limit: Number(el.torqueSlider.value)
    };
    const updated = motor.operating_mode === 0
      ? await api(`/api/motors/${motor.id}/position`, {
          method: "POST", body: { position: Number(el.targetInput.value), ...common }
        })
      : await api(`/api/motors/${motor.id}/mode-command`, {
          method: "POST", body: { value: Number(el.targetInput.value), ...common }
        });
    acceptMotorUpdate(updated, ["target", "velocity", "acceleration", "torque"]);
    render();
  } catch (error) { handleError(error); }
}

async function jogMotor(direction) {
  const motor = selectedMotor();
  if (!motor || jogPending) return;
  if (!motor.torque_enabled) return toast("Arm this motor before jogging", "warning");
  if (motor.operating_mode !== 0) return toast("Jogging is available in angle-servo mode", "warning");

  const degrees = normalizeJogDegrees(el.jogStepSelect);
  const increment = Math.max(1, Math.round(degrees * 4095 / 360));
  const base = Number(motor.target_position);

  jogPending = true;
  renderDashboard();
  try {
    const draft = controlDraft(motor);
    const updated = await api(`/api/motors/${motor.id}/jog`, {
      method: "POST",
      body: {
        delta: direction * increment,
        velocity: Number(draft.velocity),
        acceleration: Number(draft.acceleration),
        torque_limit: Number(draft.torque)
      }
    });
    acceptMotorUpdate(updated, ["target", "velocity", "acceleration", "torque"]);
    const moved = Number(updated.target_position) - base;
    toast(`ID ${motor.id} jogged ${moved < 0 ? "−" : "+"}${Math.abs(moved * 360 / 4095).toFixed(1)}° to ${updated.target_position} raw`);
  } catch (error) { handleError(error); }
  finally {
    jogPending = false;
    render();
  }
}

async function applyLimits() {
  const motor = selectedMotor();
  if (!motor) return;
  try {
    const updated = await api(`/api/motors/${motor.id}/live-parameters`, {
      method: "POST",
      body: { velocity: Number(el.velocitySlider.value), acceleration: Number(el.accelerationSlider.value), torque_limit: Number(el.torqueSlider.value) }
    });
    acceptMotorUpdate(updated, ["velocity", "acceleration", "torque"]);
    render();
  } catch (error) { handleError(error); }
}

async function captureCurrent() {
  const motor = selectedMotor();
  if (!motor) return;
  const name = prompt("Name this position", `Position ${state.captures.length + 1}`);
  if (!name) return;
  try {
    await api(`/api/motors/${motor.id}/captures`, { method: "POST", body: { name } });
  } catch (error) { handleError(error); }
}

async function goToCapture(captureId) {
  const motor = selectedMotor();
  if (!motor) return;
  try {
    await api(`/api/motors/${motor.id}/captures/${encodeURIComponent(captureId)}/go`, { method: "POST" });
  } catch (error) { handleError(error); }
}

async function deleteCapture(captureId) {
  if (!(await requestConfirmation({
    key: "delete-capture",
    eyebrow: "POSITION MEMORY",
    title: "Delete this captured position?",
    message: "This saved position will be removed from MotorLab.",
    details: ["The servo will not move.", "This captured position cannot be restored after deletion."],
    confirmLabel: "DELETE POSITION",
    skipLabel: "Don't ask again before deleting captured positions",
    tone: "danger"
  }))) return;
  try {
    state = await api(`/api/captures/${encodeURIComponent(captureId)}`, { method: "DELETE" });
    render();
  } catch (error) { handleError(error); }
}

async function writeConfig() {
  const motor = selectedMotor();
  if (!motor) return;
  if (!(await requestConfirmation({
    key: "write-persistent-config",
    eyebrow: "PERSISTENT EEPROM WRITE",
    title: `Write settings to ${motor.model} ID ${motor.id}?`,
    message: "These configuration values remain on the servo after power is removed.",
    details: [
      "Torque must remain released during the write.",
      "Each register is range-checked, written individually, and read back.",
      "Travel, thermal, voltage, and PID behavior may change."
    ],
    confirmLabel: "VERIFY & WRITE",
    skipLabel: "Don't ask again before persistent configuration writes",
    tone: "danger"
  }))) return;
  try {
    await api(`/api/motors/${motor.id}/persistent-config`, {
      method: "POST",
      body: {
        confirmation: "WRITE_EEPROM",
        values: {
          p_gain: Number(el.configP.value), d_gain: Number(el.configD.value), i_gain: Number(el.configI.value),
          min_position: Number(el.configMinPosition.value), max_position: Number(el.configMaxPosition.value),
          max_temperature: Number(el.configMaxTemp.value), min_voltage: Number(el.configMinVoltage.value),
          max_voltage: Number(el.configMaxVoltage.value)
        }
      }
    });
  } catch (error) { handleError(error); }
}

async function toggleRecording() {
  try {
    if (state?.recording?.active) {
      const recording = await api("/api/recording/stop", { method: "POST" });
      if (recording.download) toast(`Saved ${recording.name}`);
    } else {
      const label = prompt("Recording label", `${selectedMotor()?.model || "servo"}-characterization`);
      if (label === null) return;
      await api("/api/recording/start", { method: "POST", body: { label } });
    }
  } catch (error) { handleError(error); }
}

function bindControls() {
  el.themeButton.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
    requestAnimationFrame(drawChart);
  });
  el.resetConfirmationsButton.addEventListener("click", restoreSafetyConfirmations);
  el.safetyDialog.addEventListener("click", (event) => {
    if (event.target === el.safetyDialog) el.safetyDialog.close("cancel");
  });
  el.refreshPorts.addEventListener("click", refreshPorts);
  el.brandHome.addEventListener("click", (event) => { event.preventDefault(); showBusDashboard(); });
  el.busOverviewButton.addEventListener("click", () => showBusDashboard());
  el.idStationNavButton.addEventListener("click", () => showIdStation());
  el.welcomeIdStationButton.addEventListener("click", () => showIdStation());
  el.idStationBackButton.addEventListener("click", () => showBusDashboard());
  el.idModeSingle.addEventListener("change", renderIdStationMode);
  el.idModeSequential.addEventListener("change", renderIdStationMode);
  el.idStationStartButton.addEventListener("click", startIdStation);
  el.idStationStopButton.addEventListener("click", stopIdStation);
  el.idSimPlugButton.addEventListener("click", () => simulateIdStationMotor(true));
  el.idSimUnplugButton.addEventListener("click", () => simulateIdStationMotor(false));
  el.backToBusButton.addEventListener("click", () => showBusDashboard());
  el.scanButton.addEventListener("click", scanBus);
  el.connectButton.addEventListener("click", toggleConnection);
  el.torqueButton.addEventListener("click", toggleTorque);
  el.applyModeButton.addEventListener("click", applyMode);
  el.estopButton.addEventListener("click", emergencyStop);
  el.busEstopButton.addEventListener("click", emergencyStop);
  el.groupTorqueButton.addEventListener("click", toggleGroupTorque);
  el.groupCenterEncoderButton.addEventListener("click", centerAllEncoders);
  el.groupCommandButton.addEventListener("click", commandAllMotors);
  el.groupJogMinusButton.addEventListener("click", () => jogAllMotors(-1));
  el.groupJogPlusButton.addEventListener("click", () => jogAllMotors(1));
  el.groupJogStepSelect.addEventListener("change", () => {
    normalizeJogDegrees(el.groupJogStepSelect);
    renderGroupControls(state?.motors || []);
  });
  GROUP_CONFIG_FIELDS.forEach((field) => {
    el[field.element].addEventListener("input", () => {
      if (el[field.element].value === "") groupConfigDirty.delete(field.key);
      else groupConfigDirty.add(field.key);
      renderGroupConfigEditor(state?.motors || []);
    });
  });
  el.groupWriteConfigButton.addEventListener("click", writeAllConfig);
  el.centerEncoderButton.addEventListener("click", centerMotorEncoder);
  el.goButton.addEventListener("click", sendTarget);
  el.jogMinusButton.addEventListener("click", () => jogMotor(-1));
  el.jogPlusButton.addEventListener("click", () => jogMotor(1));
  el.jogStepSelect.addEventListener("change", () => {
    normalizeJogDegrees(el.jogStepSelect);
    renderDashboard();
  });
  el.torqueRatedMarker.addEventListener("click", () => {
    const motor = selectedMotor();
    const ratedLimit = motor?.rated_torque_limit_raw;
    if (ratedLimit == null) return;
    updateDraft("torque", ratedLimit);
    el.torqueSlider.value = ratedLimit;
    renderTorqueLimitOutput(ratedLimit, motor);
    updateRange(el.torqueSlider);
    toast(`${motor.model} nominal rated-torque limit staged at ${ratedLimit} raw (${motor.rated_torque_kgcm} kgf·cm) — press Apply limits to send`);
  });
  el.applyParameters.addEventListener("click", applyLimits);
  el.captureButton.addEventListener("click", captureCurrent);
  el.writeConfigButton.addEventListener("click", writeConfig);
  el.recordButton.addEventListener("click", toggleRecording);
  el.centerButton.addEventListener("click", () => {
    const mode = CONTROL_MODES[selectedMotor()?.operating_mode] || CONTROL_MODES[0];
    updateDraft("target", mode.neutral);
    el.targetInput.value = mode.neutral;
    el.targetSlider.value = mode.neutral;
    updateRange(el.targetSlider);
    toast(mode.neutral === 0
      ? `Zero-motion ${mode.name.toLowerCase()} command staged — press ${mode.action.toLowerCase()} to send it`
      : "Target set to the center zone — press Go to target when ready", "success");
  });
  el.modeSelect.addEventListener("change", () => {
    updateDraft("mode", el.modeSelect.value);
    renderDashboard();
  });
  el.targetSlider.addEventListener("input", () => {
    updateDraft("target", el.targetSlider.value);
    el.targetInput.value = el.targetSlider.value;
    updateRange(el.targetSlider);
  });
  el.targetInput.addEventListener("input", () => {
    const minimum = Number(el.targetInput.min);
    const maximum = Number(el.targetInput.max);
    const value = Math.max(minimum, Math.min(maximum, Number(el.targetInput.value) || 0));
    updateDraft("target", value);
    el.targetSlider.value = value;
    updateRange(el.targetSlider);
  });
  [[el.velocitySlider, el.velocityOutput, "velocity"], [el.accelerationSlider, el.accelerationOutput, "acceleration"]].forEach(([input, output, key]) => {
    input.addEventListener("input", () => {
      updateDraft(key, input.value);
      output.value = input.value;
      updateRange(input);
    });
    output.addEventListener("input", () => {
      if (output.value === "") return;
      const value = boundedRangeValue(input, output.value);
      input.value = value;
      updateDraft(key, value);
      updateRange(input);
    });
    output.addEventListener("change", () => {
      const value = boundedRangeValue(input, output.value);
      output.value = value;
      input.value = value;
      updateDraft(key, value);
      updateRange(input);
    });
  });
  el.torqueSlider.addEventListener("input", () => {
    updateDraft("torque", el.torqueSlider.value);
    renderTorqueLimitOutput(el.torqueSlider.value);
    updateRange(el.torqueSlider);
  });
  el.torqueRawOutput.addEventListener("input", () => {
    if (el.torqueRawOutput.value === "") return;
    const value = boundedRangeValue(el.torqueSlider, el.torqueRawOutput.value);
    el.torqueSlider.value = value;
    updateDraft("torque", value);
    updateRange(el.torqueSlider);
  });
  el.torqueRawOutput.addEventListener("change", () => {
    const value = boundedRangeValue(el.torqueSlider, el.torqueRawOutput.value);
    el.torqueRawOutput.value = value;
    el.torqueSlider.value = value;
    updateDraft("torque", value);
    renderTorqueLimitOutput(value);
    updateRange(el.torqueSlider);
  });
  el.groupTargetSlider.addEventListener("input", () => {
    el.groupTargetInput.value = el.groupTargetSlider.value;
    updateRange(el.groupTargetSlider);
  });
  el.groupTargetInput.addEventListener("input", () => {
    const value = Math.max(0, Math.min(4095, Number(el.groupTargetInput.value) || 0));
    el.groupTargetSlider.value = value;
    updateRange(el.groupTargetSlider);
  });
  [
    [el.groupVelocitySlider, el.groupVelocityOutput],
    [el.groupAccelerationSlider, el.groupAccelerationOutput],
    [el.groupTorqueSlider, el.groupTorqueOutput]
  ].forEach(([input, output]) => {
    input.addEventListener("input", () => {
      output.value = input.value;
      updateRange(input);
    });
    output.addEventListener("input", () => {
      if (output.value === "") return;
      input.value = boundedRangeValue(input, output.value);
      updateRange(input);
    });
    output.addEventListener("change", () => {
      const value = boundedRangeValue(input, output.value);
      output.value = value;
      input.value = value;
      updateRange(input);
    });
  });
  window.addEventListener("resize", drawChart);
  window.addEventListener("popstate", applyRoute);
  document.addEventListener("keydown", (event) => {
    if (event.code === "Space" && !["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(document.activeElement?.tagName)) {
      event.preventDefault(); emergencyStop();
    }
  });
}

function connectSocket() {
  if (socket) socket.close();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.addEventListener("message", (event) => {
    state = JSON.parse(event.data);
    render();
  });
  socket.addEventListener("close", () => setTimeout(connectSocket, 1200));
}

function signed(value) {
  const number = Number(value);
  return number > 0 ? `+${value}` : String(value);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

async function initialize() {
  setTheme(document.documentElement.dataset.theme);
  updateConfirmationPreferenceControl();
  installRangeKeypoints();
  bindControls();
  try {
    const bootstrap = await api("/api/bootstrap");
    state = bootstrap.state;
    ports = bootstrap.ports;
    profiles = bootstrap.profiles;
    renderPorts();
    if (!location.hash) window.history.replaceState(null, "", "#bus");
    await applyRoute();
    connectSocket();
  } catch (error) { handleError(error); }
}

initialize();
