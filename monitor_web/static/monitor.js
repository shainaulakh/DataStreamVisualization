const settings = window.MONITOR;
const pointsInWindow = Math.round(settings.window / settings.tick);
const trackScale = 1.25;
const eventRowsKept = 8;

const traceTitle = document.getElementById("trace-title");
const note = document.getElementById("note");
const stateBadge = document.getElementById("state");
const healthBadge = document.getElementById("health");
const eventsBody = document.getElementById("events");
const meterRows = [...document.querySelectorAll("#meters li")];
const realtimeButton = document.getElementById("realtime-view");
const historyButton = document.getElementById("history-view");

let feed = null;
let mode = "realtime";
let eventCount = 0;

const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const chart = new Chart(document.getElementById("trace"), {
  type: "line",
  data: { labels: [], datasets: [] },
  options: {
    animation: false,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false, align: "start", labels: { boxWidth: 12, boxHeight: 2 } },
      tooltip: { callbacks: { label: (item) => ` ${item.dataset.label}: ${item.parsed.y.toFixed(1)} A` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
      y: { beginAtZero: true, suggestedMax: 90, ticks: { callback: (value) => `${value} A` }, border: { display: false } },
    },
  },
});

function paintTheme() {
  chart.options.color = token("--ink-2");
  chart.options.scales.x.ticks.color = token("--muted");
  chart.options.scales.y.ticks.color = token("--muted");
  chart.options.scales.x.border = { color: token("--baseline") };
  chart.options.scales.y.grid.color = token("--grid");
  for (const dataset of chart.data.datasets) {
    Object.assign(dataset, dataset.role === "context"
      ? { borderColor: token("--context") }
      : { borderColor: token("--accent"), backgroundColor: token("--accent-wash") });
  }
  chart.update("none");
}

function totalSeries(label) {
  return { label, role: "focus", data: [], fill: true, borderWidth: 2, pointRadius: 0, tension: 0.25 };
}

function setMode(next) {
  mode = next;
  realtimeButton.classList.toggle("selected", next === "realtime");
  historyButton.classList.toggle("selected", next === "history");
}

function placeholderRow() {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  row.className = "empty";
  cell.colSpan = 5;
  cell.textContent = "None yet";
  row.append(cell);
  return row;
}

function resetPanels() {
  eventCount = 0;
  eventsBody.replaceChildren(placeholderRow());
  healthBadge.className = "badge good";
  healthBadge.querySelector(".text").textContent = "✓ Health OK";
  for (const row of meterRows) {
    row.classList.remove("over");
    row.querySelector(".fill").style.width = "0";
    row.querySelector(".value").textContent = "0.0 A";
  }
}

function logEvent(reading, joint, amps, limit) {
  eventCount += 1;
  eventsBody.querySelector(".empty")?.remove();
  const over = ((amps / limit - 1) * 100).toFixed(1);
  const row = document.createElement("tr");
  for (const cell of [reading.at, settings.labels[joint], `${amps.toFixed(1)} A`, `${limit.toFixed(1)} A`, `+${over}%`]) {
    const td = document.createElement("td");
    td.textContent = cell;
    row.append(td);
  }
  eventsBody.prepend(row);
  while (eventsBody.rows.length > eventRowsKept) eventsBody.lastElementChild.remove();
  healthBadge.className = "badge warning";
  healthBadge.querySelector(".text").textContent = `⚠ ${eventCount} anomaly event${eventCount === 1 ? "" : "s"}`;
}

function paintMeters(reading, calibrating) {
  for (const row of meterRows) {
    const joint = row.dataset.joint;
    const limit = Number(row.dataset.limit);
    const amps = reading.amps[joint];
    const over = amps > limit;
    row.classList.toggle("over", over);
    row.querySelector(".fill").style.width = `${Math.min(amps / (limit * trackScale), 1) * 100}%`;
    row.querySelector(".value").textContent = `${over ? "▲ " : ""}${amps.toFixed(1)} A`;
    if (over && !calibrating) logEvent(reading, joint, amps, limit);
  }
}

function paintState(reading) {
  stateBadge.className = `badge ${reading.state === "RUNNING" ? "running" : ""}`;
  stateBadge.querySelector(".text").textContent = reading.state === "RUNNING" ? "Running" : "Idle";
}

function onReading(message) {
  if (mode !== "realtime") return;
  const reading = JSON.parse(message.data);
  const labels = chart.data.labels;
  const values = chart.data.datasets[0].data;
  labels.push(reading.at);
  values.push(reading.total);
  if (labels.length > pointsInWindow) {
    labels.shift();
    values.shift();
  }
  chart.update("none");
  traceTitle.textContent = `Total current, last 90 seconds · ${reading.at} UTC · ${reading.total.toFixed(1)} A`;
  const calibrating = reading.id <= settings.calibrationLastId;
  if (calibrating) {
    healthBadge.className = "badge";
    healthBadge.querySelector(".text").textContent = "Calibrating limits";
  } else if (eventCount === 0) {
    healthBadge.className = "badge good";
    healthBadge.querySelector(".text").textContent = "✓ Health OK";
  }
  paintMeters(reading, calibrating);
  paintState(reading);
}

function stopFeed() {
  feed?.close();
  feed = null;
}

function startRealtime() {
  stopFeed();
  setMode("realtime");
  resetPanels();
  chart.data.labels = [];
  chart.data.datasets = [totalSeries("Total current")];
  chart.options.plugins.legend.display = false;
  paintTheme();
  traceTitle.textContent = "Total current, last 90 seconds";
  note.textContent = `Replaying from the first reading, one every ${settings.tick} s. The limits come from the first hour (${settings.calibration}), so anomaly checks start after it.`;
  feed = new EventSource("/api/stream?after=0");
  feed.onmessage = onReading;
  feed.addEventListener("finished", () => {
    stopFeed();
    note.textContent = "End of the recorded shift.";
  });
}

async function showHistory() {
  stopFeed();
  setMode("history");
  note.textContent = "Loading the full history from Neon…";
  const response = await fetch("/api/history");
  const summary = await response.json();
  if (mode !== "history") return;
  chart.data.labels = summary.minutes;
  const peak = { ...totalSeries("Peak in each minute"), data: summary.peak };
  const mean = { label: "Average in each minute", role: "context", data: summary.mean, fill: false, borderWidth: 1.5, pointRadius: 0, tension: 0.2 };
  chart.data.datasets = [peak, mean];
  chart.options.plugins.legend.display = true;
  paintTheme();
  traceTitle.textContent = `Full history: ${summary.readings.toLocaleString()} readings as per-minute peak and average`;
  note.textContent = "Real-time replay paused. Press Real-time to start it again.";
}

realtimeButton.addEventListener("click", startRealtime);
historyButton.addEventListener("click", showHistory);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);

startRealtime();
