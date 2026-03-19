import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import type {
  GpuDiagnostics,
  GpuDiscovery,
  ResultRow,
  ScanStatus,
  Settings,
  Target,
} from "./types";

type Tab = "dashboard" | "targets" | "results" | "settings";

const TAB_HASH_MAP: Record<Tab, string> = {
  dashboard: "#dashboard",
  targets: "#targets",
  results: "#results",
  settings: "#settings",
};

function parseTabFromHash(hash: string): Tab | null {
  const clean = hash.replace(/^#/, "").trim().toLowerCase();
  if (clean === "dashboard") return "dashboard";
  if (clean === "targets") return "targets";
  if (clean === "results") return "results";
  if (clean === "settings") return "settings";
  return null;
}

const DEFAULT_SETTINGS: Settings = {
  general_discord_webhook: "",
  failed_discord_webhook: "",
  scan_interval_seconds: "3600",
  video_extensions: ".mp4,.mkv,.avi,.mov,.flv,.wmv",
  gpu_enabled: false,
  gpu_backend: "auto",
  gpu_device_id: "0",
};

const DEFAULT_GPU_DISCOVERY: GpuDiscovery = {
  hwaccels: [],
  backends: [],
  devices: [],
  warnings: [],
};

const DEFAULT_GPU_DIAGNOSTICS: GpuDiagnostics = {
  summary: {
    gpu_enabled: false,
    selected_backend: "auto",
    resolved_backend: "cpu",
    selected_device: "0",
    healthy: true,
  },
  environment: {
    NVIDIA_VISIBLE_DEVICES: "",
    NVIDIA_DRIVER_CAPABILITIES: "",
  },
  checks: [],
  probes: [],
};

const DEFAULT_SCAN_STATUS: ScanStatus = {
  running: false,
  last_started: null,
  last_completed: null,
  last_summary: {},
  files_total: 0,
  files_done: 0,
  current_file: "",
  current_file_path: "",
  current_file_started_at: null,
  current_file_elapsed_seconds: 0,
  current_target: "",
  recent_logs: [],
  persisted_results_count: 0,
  db_target: "",
  active_rescan: {
    result_id: null,
    file_path: "",
    started_at: null,
    elapsed_seconds: 0,
  },
  queued_rescans: [],
};

function formatDate(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function formatElapsedSeconds(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "0s";
  }
  const wholeSeconds = Math.floor(value);
  if (wholeSeconds < 60) {
    return `${wholeSeconds}s`;
  }
  if (wholeSeconds < 3600) {
    const minutes = Math.floor(wholeSeconds / 60);
    const seconds = wholeSeconds - minutes * 60;
    return `${minutes}min ${seconds}s`;
  }
  const hours = Math.floor(wholeSeconds / 3600);
  const remainderAfterHours = wholeSeconds - hours * 3600;
  const minutes = Math.floor(remainderAfterHours / 60);
  const seconds = remainderAfterHours - minutes * 60;
  return `${hours}h ${minutes}min ${seconds}s`;
}

function toBoolean(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (value === null || value === undefined) {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function normalizeSettings(raw: Record<string, unknown>): Settings {
  return {
    general_discord_webhook: String(
      raw.general_discord_webhook ?? DEFAULT_SETTINGS.general_discord_webhook
    ),
    failed_discord_webhook: String(raw.failed_discord_webhook ?? DEFAULT_SETTINGS.failed_discord_webhook),
    scan_interval_seconds: String(raw.scan_interval_seconds ?? DEFAULT_SETTINGS.scan_interval_seconds),
    video_extensions: String(raw.video_extensions ?? DEFAULT_SETTINGS.video_extensions),
    gpu_enabled: toBoolean(raw.gpu_enabled ?? DEFAULT_SETTINGS.gpu_enabled),
    gpu_backend: String(raw.gpu_backend ?? DEFAULT_SETTINGS.gpu_backend).toLowerCase() || "auto",
    gpu_device_id: String(raw.gpu_device_id ?? DEFAULT_SETTINGS.gpu_device_id),
  };
}

export default function App() {
  const [tab, setTab] = useState<Tab>(() => parseTabFromHash(window.location.hash) || "dashboard");
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [targets, setTargets] = useState<Target[]>([]);
  const [results, setResults] = useState<ResultRow[]>([]);
  const [scanStatus, setScanStatus] = useState<ScanStatus>(DEFAULT_SCAN_STATUS);
  const [summary, setSummary] = useState<Record<string, Record<string, number>>>({});
  const [dbLastScan, setDbLastScan] = useState<string | null>(null);
  const [dbTotalResults, setDbTotalResults] = useState(0);
  const [dbTotalErrors, setDbTotalErrors] = useState(0);
  const [newLabel, setNewLabel] = useState("");
  const [newPath, setNewPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [gpuDiscovery, setGpuDiscovery] = useState<GpuDiscovery>(DEFAULT_GPU_DISCOVERY);
  const [gpuDiagnostics, setGpuDiagnostics] = useState<GpuDiagnostics>(DEFAULT_GPU_DIAGNOSTICS);
  const [gpuDiagnosticsLoading, setGpuDiagnosticsLoading] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState("/media");
  const [browserDirs, setBrowserDirs] = useState<{ name: string; path: string }[]>([]);
  const [browserParent, setBrowserParent] = useState("/media");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [rescanningResultIds, setRescanningResultIds] = useState<Set<number>>(new Set());
  const wasRunning = useRef(false);

  const refreshGpuData = useCallback(async () => {
    setGpuDiagnosticsLoading(true);
    const [discoveryResult, diagnosticsResult] = await Promise.allSettled([
      api.getGpuDiscovery(),
      api.getGpuDiagnostics(),
    ]);

    if (discoveryResult.status === "fulfilled") {
      setGpuDiscovery(discoveryResult.value);
    } else {
      setGpuDiscovery(DEFAULT_GPU_DISCOVERY);
    }

    if (diagnosticsResult.status === "fulfilled") {
      setGpuDiagnostics(diagnosticsResult.value);
    } else {
      setGpuDiagnostics(DEFAULT_GPU_DIAGNOSTICS);
    }
    setGpuDiagnosticsLoading(false);
  }, []);

  const refreshAll = useCallback(async () => {
    const [settingsData, targetsData, resultsData, statusData, summaryData] = await Promise.all([
      api.getSettings(),
      api.getTargets(),
      api.getResults(),
      api.getScanStatus(),
      api.getSummary(),
    ]);
    setSettings(normalizeSettings(settingsData as Record<string, unknown>));
    setTargets(targetsData);
    setResults(resultsData);
    setScanStatus({ ...DEFAULT_SCAN_STATUS, ...statusData });
    setSummary(summaryData.by_target || {});
    setDbLastScan(summaryData.last_scan || null);
    setDbTotalResults(summaryData.total_results || 0);
    setDbTotalErrors(summaryData.total_errors || 0);
    await refreshGpuData();
  }, [refreshGpuData]);

  useEffect(() => {
    refreshAll().catch(() => setMessage("Failed to load data"));
  }, [refreshAll]);

  useEffect(() => {
    const onHashChange = () => {
      const fromHash = parseTabFromHash(window.location.hash);
      if (fromHash && fromHash !== tab) {
        setTab(fromHash);
      }
    };

    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [tab]);

  useEffect(() => {
    const desiredHash = TAB_HASH_MAP[tab];
    if (window.location.hash !== desiredHash) {
      window.history.replaceState(null, "", desiredHash);
    }
  }, [tab]);

  useEffect(() => {
    const pollInterval = scanStatus.running ? 1000 : 5000;
    const interval = setInterval(() => {
      api
        .getScanStatus()
        .then((status) => {
          const merged = { ...DEFAULT_SCAN_STATUS, ...status };
          setScanStatus(merged);
          if (wasRunning.current && !merged.running) {
            setMessage("Scan completed");
            refreshAll().catch(() => undefined);
          }
          wasRunning.current = merged.running;
        })
        .catch(() => undefined);
    }, pollInterval);

    return () => clearInterval(interval);
  }, [scanStatus.running, refreshAll]);

  const errorCount = useMemo(
    () => results.filter((item) => item.status !== "OK").length,
    [results]
  );

  const totalScanned = dbTotalResults;

  const totalErrors = dbTotalErrors;

  const effectiveLastCompleted = scanStatus.last_completed || dbLastScan;

  const hasMediaTarget = useMemo(
    () => targets.some((target) => target.path === "/media" || target.path.startsWith("/media/")),
    [targets]
  );

  const showMediaWarning = targets.length === 0 || !hasMediaTarget;

  const displayedResults = useMemo(
    () => (errorsOnly ? results.filter((r) => r.status !== "OK") : results),
    [results, errorsOnly]
  );

  const filteredGpuDevices = useMemo(() => {
    if (settings.gpu_backend === "auto") {
      return gpuDiscovery.devices;
    }
    return gpuDiscovery.devices.filter((device) => device.backend === settings.gpu_backend);
  }, [gpuDiscovery.devices, settings.gpu_backend]);

  const progressPct = useMemo(() => {
    if (scanStatus.files_total <= 0) {
      return 0;
    }
    return Math.min(100, Math.round((scanStatus.files_done / scanStatus.files_total) * 100));
  }, [scanStatus.files_done, scanStatus.files_total]);

  const recentLogs = useMemo(
    () => [...scanStatus.recent_logs].slice(-80).reverse(),
    [scanStatus.recent_logs]
  );
  const latestLogEntry = recentLogs[0] ?? null;

  async function saveSettings() {
    setSaving(true);
    setMessage("");
    try {
      await api.updateSettings({
        general_discord_webhook: settings.general_discord_webhook,
        failed_discord_webhook: settings.failed_discord_webhook,
        scan_interval_seconds: Number(settings.scan_interval_seconds || "3600"),
        video_extensions: settings.video_extensions,
        gpu_enabled: settings.gpu_enabled,
        gpu_backend: settings.gpu_backend,
        gpu_device_id: settings.gpu_device_id,
      });
      setMessage("Settings saved");
      await refreshAll();
    } catch {
      setMessage("Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function addTarget() {
    if (!newLabel.trim() || !newPath.trim()) {
      return;
    }
    await api.createTarget({ label: newLabel.trim(), path: newPath.trim(), enabled: true });
    setNewLabel("");
    setNewPath("");
    await refreshAll();
  }

  async function toggleTarget(target: Target) {
    await api.updateTarget(target.id, {
      label: target.label,
      path: target.path,
      enabled: !target.enabled,
    });
    await refreshAll();
  }

  async function removeTarget(id: number) {
    await api.deleteTarget(id);
    await refreshAll();
  }

  async function runScan() {
    const response = await api.triggerScan();
    if (response.status === "already-running") {
      setMessage("A scan is already running");
      return;
    }
    setMessage("Scan started");
    refreshAll().catch(() => undefined);
  }

  async function openBrowser(path = "/media") {
    try {
      const data = await api.browseTargets(path);
      setBrowserPath(data.path);
      setBrowserParent(data.parent);
      setBrowserDirs(data.directories);
      setBrowserOpen(true);
    } catch {
      setMessage("Unable to browse folders. Check /media mount and permissions.");
    }
  }

  async function rescanResult(resultId: number) {
    setRescanningResultIds((previous) => {
      const next = new Set(previous);
      next.add(resultId);
      return next;
    });
    try {
      await api.rescanResult(resultId);
      setMessage("Result rescanned");
      await refreshAll();
    } catch {
      setMessage("Failed to rescan result");
    } finally {
      setRescanningResultIds((previous) => {
        const next = new Set(previous);
        next.delete(resultId);
        return next;
      });
    }
  }

  return (
    <div className="page">
      <div className="layout">
        <aside className="sidebar card">
          <div className="sidebar-top">
            <h1>Video Error Checker</h1>
            <button onClick={runScan} disabled={scanStatus.running}>
              {scanStatus.running ? "Scan Running" : "Run Scan Now"}
            </button>
            <p className="scan-db-meta">
              DB: {scanStatus.db_target || "unknown"} • Persisted rows: {scanStatus.persisted_results_count}
            </p>
            <p className="sidebar-log">
              {latestLogEntry
                ? `${formatDate(latestLogEntry.timestamp)} — ${latestLogEntry.message}`
                : "No scan activity yet"}
            </p>
          </div>

          <nav>
            <ul className="nav-list">
              <li>
                <a
                  href="#"
                  className={tab === "dashboard" ? "nav-link active" : "nav-link"}
                  onClick={(event) => {
                    event.preventDefault();
                    setTab("dashboard");
                  }}
                >
                  Dashboard
                </a>
              </li>
              <li>
                <a
                  href="#"
                  className={tab === "targets" ? "nav-link active" : "nav-link"}
                  onClick={(event) => {
                    event.preventDefault();
                    setTab("targets");
                  }}
                >
                  Scan Targets
                </a>
              </li>
              <li>
                <a
                  href="#"
                  className={tab === "results" ? "nav-link active" : "nav-link"}
                  onClick={(event) => {
                    event.preventDefault();
                    setTab("results");
                  }}
                >
                  Results
                </a>
              </li>
              <li>
                <a
                  href="#"
                  className={tab === "settings" ? "nav-link active" : "nav-link"}
                  onClick={(event) => {
                    event.preventDefault();
                    setTab("settings");
                  }}
                >
                  Settings
                </a>
              </li>
            </ul>
          </nav>
        </aside>

        <main className="content">
          {message ? <p className="message">{message}</p> : null}
          {showMediaWarning ? (
            <p className="warning">
              Add at least one scan target under /media (for example: /media/tv or /media/movies).
            </p>
          ) : null}

          {tab === "dashboard" && (
            <>
              <section className="card-grid">
                <div className="card">
                  <h3>Enabled Targets</h3>
                  <p>{targets.filter((target) => target.enabled).length}</p>
                </div>
                <div className="card">
                  <h3>Total Scanned Files</h3>
                  <p>{totalScanned}</p>
                  {scanStatus.persisted_results_count > totalScanned ? (
                    <p className="message">Live DB rows: {scanStatus.persisted_results_count}</p>
                  ) : null}
                </div>
                <div className="card">
                  <h3>Total Errors</h3>
                  <p>{totalErrors}</p>
                </div>
                <div className="card">
                  <h3>Last Completed</h3>
                  <p>{formatDate(effectiveLastCompleted)}</p>
                </div>
                <div className="card">
                  <h3>Scan Queue</h3>
                  <p>
                    {scanStatus.running
                      ? `${scanStatus.current_file || "(starting...)"} (${formatElapsedSeconds(scanStatus.current_file_elapsed_seconds)})`
                      : "IDLE"}
                  </p>
                </div>
                <div className="card">
                  <h3>Rescan Queue</h3>
                  <p>
                    {scanStatus.active_rescan.result_id !== null
                      ? `#${scanStatus.active_rescan.result_id} (${formatElapsedSeconds(scanStatus.active_rescan.elapsed_seconds)})`
                      : "IDLE"}
                  </p>
                  <p className="scan-db-meta">Queued: {scanStatus.queued_rescans.length}</p>
                </div>
              </section>

              <section className="card scan-progress">
                <div className="scan-progress-header">
                  <strong>
                    {scanStatus.running
                      ? scanStatus.current_target
                        ? `Scanning ${scanStatus.current_target}`
                        : "Scanning"
                      : "Scan Status"}
                  </strong>
                  <span>
                    {scanStatus.running
                      ? scanStatus.files_total > 0
                        ? `${scanStatus.files_done} / ${scanStatus.files_total} files`
                        : scanStatus.files_done > 0
                          ? `${scanStatus.files_done} files indexed...`
                          : "Preparing file list..."
                      : "IDLE"}
                  </span>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
                <p className="scan-progress-file">
                  {scanStatus.running
                    ? scanStatus.current_file || "Waiting for first file..."
                    : "IDLE"}
                </p>
                <p className="scan-db-meta">
                  {scanStatus.running && scanStatus.current_file
                    ? `Current file runtime: ${formatElapsedSeconds(scanStatus.current_file_elapsed_seconds)}`
                    : "Current file runtime: IDLE"}
                </p>
              </section>

              <section className="card scan-log-panel">
                <div className="results-header">
                  <h3>Live Scan Activity</h3>
                  <span>{scanStatus.running ? "Live" : "Last run"}</span>
                </div>
                {recentLogs.length === 0 ? (
                  <p className="scan-db-meta">No log entries yet.</p>
                ) : (
                  <div className="scan-log-list">
                    {recentLogs.map((entry, index) => (
                      <div className="scan-log-row" key={`${entry.timestamp}-${index}`}>
                        <span className="scan-log-time">{formatDate(entry.timestamp)}</span>
                        <span className={`scan-log-level scan-log-${entry.level}`}>{entry.level}</span>
                        <span className="scan-log-message">
                          {entry.source ? (
                            <span className={`scan-log-source scan-log-source-${entry.source}`}>
                              {entry.source === "rescan" ? "RESCAN" : entry.source.toUpperCase()}
                            </span>
                          ) : null}
                          {entry.message}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="card-grid">
                <div className="card full">
                  <h3>Last Scan Details</h3>
                  <p>Started: {formatDate(scanStatus.last_started)}</p>
                  <p>Completed: {formatDate(effectiveLastCompleted)}</p>
                  {scanStatus.last_started === null && dbLastScan ? (
                    <p className="message">Runtime restarted since last completed scan; using DB history.</p>
                  ) : null}
                  <p>
                    Last run summary:{" "}
                    {Object.keys(scanStatus.last_summary).length === 0
                      ? "No run yet"
                      : JSON.stringify(scanStatus.last_summary)}
                  </p>
                </div>
              </section>
            </>
          )}

          {tab === "targets" && (
            <section className="card">
          <h3>Add Target</h3>
          <div className="row">
            <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder="Label (e.g. TV)" />
            <input value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="Path (e.g. /media/tv)" />
            <div className="row-actions">
              <button onClick={() => openBrowser(newPath || "/media")}>Browse</button>
              <button onClick={addTarget}>Add</button>
            </div>
          </div>
          {browserOpen ? (
            <div className="browser">
              <div className="browser-header">
                <strong>{browserPath}</strong>
                <div className="row-actions">
                  <button onClick={() => openBrowser(browserParent)}>Up</button>
                  <button
                    onClick={() => {
                      setNewPath(browserPath);
                      setBrowserOpen(false);
                    }}
                  >
                    Use This Folder
                  </button>
                  <button onClick={() => setBrowserOpen(false)}>Close</button>
                </div>
              </div>
              <div className="browser-list">
                {browserDirs.length === 0 ? (
                  <p>No subfolders found.</p>
                ) : (
                  browserDirs.map((directory) => (
                    <button key={directory.path} onClick={() => openBrowser(directory.path)}>
                      {directory.name}
                    </button>
                  ))
                )}
              </div>
            </div>
          ) : null}
          <table>
            <thead>
              <tr>
                <th>Label</th>
                <th>Path</th>
                <th>Enabled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {targets.map((target) => (
                <tr key={target.id}>
                  <td>{target.label}</td>
                  <td>{target.path}</td>
                  <td>{String(target.enabled)}</td>
                  <td>
                    <button onClick={() => toggleTarget(target)}>
                      {target.enabled ? "Disable" : "Enable"}
                    </button>
                    <button onClick={() => removeTarget(target.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </section>
          )}

          {tab === "results" && (
            <section className="card">
          <div className="results-header">
            <h3>Recent Results</h3>
            <div className="row-actions">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={errorsOnly}
                  onChange={(e) => setErrorsOnly(e.target.checked)}
                />
                Errors only ({errorCount})
              </label>
              <button onClick={() => refreshAll().catch(() => undefined)}>Refresh</button>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Target</th>
                <th>File</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Scanned At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {displayedResults.map((result) => (
                <tr key={result.id}>
                  <td>{result.label}</td>
                  <td className="path">{result.file_path}</td>
                  <td className={result.status === "OK" ? "ok" : "error"}>{result.status}</td>
                  <td>{formatElapsedSeconds(result.scan_duration_seconds)}</td>
                  <td>{formatDate(result.scanned_at)}</td>
                  <td>
                    {result.status !== "OK" ? (
                      (() => {
                        const rowIsQueued = result.status === "Rescan Queued";
                        const rowIsRescanning =
                          rescanningResultIds.has(result.id) || result.status === "Rescanning";
                        const rowBusy = rowIsQueued || rowIsRescanning;
                        return (
                      <button
                        onClick={() => rescanResult(result.id)}
                        disabled={rowBusy}
                      >
                        {rowIsQueued ? "Queued..." : rowIsRescanning ? "Rescanning..." : "Rescan"}
                      </button>
                        );
                      })()
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </section>
          )}

          {tab === "settings" && (
            <section className="card form">
          <label>
            General Discord Webhook
            <input
              value={settings.general_discord_webhook}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, general_discord_webhook: e.target.value }))
              }
            />
          </label>
          <label>
            Failed Check Webhook
            <input
              value={settings.failed_discord_webhook}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, failed_discord_webhook: e.target.value }))
              }
            />
          </label>
          <label>
            Scan Interval Seconds
            <input
              value={settings.scan_interval_seconds}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, scan_interval_seconds: e.target.value }))
              }
            />
          </label>
          <label>
            Video Extensions (comma separated)
            <input
              value={settings.video_extensions}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, video_extensions: e.target.value }))
              }
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={settings.gpu_enabled}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, gpu_enabled: e.target.checked }))
              }
            />
            Enable GPU hardware offload
          </label>
          <label>
            GPU Backend
            <select
              value={settings.gpu_backend}
              disabled={!settings.gpu_enabled}
              onChange={(e) => {
                const nextBackend = e.target.value;
                setSettings((prev) => {
                  const candidates =
                    nextBackend === "auto"
                      ? gpuDiscovery.devices
                      : gpuDiscovery.devices.filter((device) => device.backend === nextBackend);
                  const nextDevice = candidates.some((device) => device.id === prev.gpu_device_id)
                    ? prev.gpu_device_id
                    : (candidates[0]?.id ?? "0");
                  return {
                    ...prev,
                    gpu_backend: nextBackend,
                    gpu_device_id: nextDevice,
                  };
                });
              }}
            >
              {gpuDiscovery.backends.length === 0 ? (
                <option value="auto">Auto</option>
              ) : (
                gpuDiscovery.backends.map((backend) => (
                  <option key={backend.id} value={backend.id}>
                    {backend.label}
                    {!backend.available ? " (unavailable)" : ""}
                  </option>
                ))
              )}
            </select>
          </label>
          <label>
            GPU Device
            <select
              value={settings.gpu_device_id}
              disabled={!settings.gpu_enabled || filteredGpuDevices.length === 0}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, gpu_device_id: e.target.value }))
              }
            >
              {filteredGpuDevices.length === 0 ? (
                <option value="0">No GPU device discovered</option>
              ) : (
                filteredGpuDevices.map((device) => (
                  <option key={`${device.backend}-${device.id}`} value={device.id}>
                    {device.label}
                    {!device.usable && device.reason ? ` (${device.reason})` : ""}
                  </option>
                ))
              )}
            </select>
          </label>
          <div className="row-actions">
            <button onClick={() => refreshGpuData().catch(() => undefined)} disabled={gpuDiagnosticsLoading}>
              {gpuDiagnosticsLoading ? "Refreshing diagnostics..." : "Refresh GPU Diagnostics"}
            </button>
          </div>
          {gpuDiscovery.warnings.length > 0 ? (
            <div className="warning">
              {gpuDiscovery.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          ) : null}
          <div className="card">
            <h3>GPU Diagnostics</h3>
            <p>
              Health: {gpuDiagnostics.summary.healthy ? "Healthy" : "Needs attention"} | Selected: {gpuDiagnostics.summary.selected_backend} / Resolved: {gpuDiagnostics.summary.resolved_backend} / device {gpuDiagnostics.summary.selected_device || "N/A"}
            </p>
            <p>
              NVIDIA_VISIBLE_DEVICES: {gpuDiagnostics.environment.NVIDIA_VISIBLE_DEVICES || "(unset)"}
            </p>
            <p>
              NVIDIA_DRIVER_CAPABILITIES: {gpuDiagnostics.environment.NVIDIA_DRIVER_CAPABILITIES || "(unset)"}
            </p>
            <table>
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Status</th>
                  <th>Message</th>
                  <th>Hint</th>
                </tr>
              </thead>
              <tbody>
                {gpuDiagnostics.checks.length === 0 ? (
                  <tr>
                    <td colSpan={4}>No diagnostics available yet.</td>
                  </tr>
                ) : (
                  gpuDiagnostics.checks.map((check) => (
                    <tr key={check.id}>
                      <td>{check.id}</td>
                      <td>{check.ok ? "OK" : check.severity.toUpperCase()}</td>
                      <td>{check.message}</td>
                      <td>{check.hint || "-"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            {gpuDiagnostics.probes.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Probe</th>
                    <th>Status</th>
                    <th>Message</th>
                    <th>Hint</th>
                  </tr>
                </thead>
                <tbody>
                  {gpuDiagnostics.probes.map((probe) => (
                    <tr key={`${probe.backend}-${probe.device_id}`}>
                      <td>{probe.backend} / {probe.device_id}</td>
                      <td>{probe.ok ? "OK" : "FAILED"}</td>
                      <td>{probe.message}</td>
                      <td>{probe.hint || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </div>
          <button onClick={saveSettings} disabled={saving}>
            {saving ? "Saving..." : "Save Settings"}
          </button>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
