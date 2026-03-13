import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import type { ResultRow, ScanStatus, Settings, Target } from "./types";

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
};

const DEFAULT_SCAN_STATUS: ScanStatus = {
  running: false,
  last_started: null,
  last_completed: null,
  last_summary: {},
  files_total: 0,
  files_done: 0,
  current_file: "",
  current_target: "",
  recent_logs: [],
  persisted_results_count: 0,
  db_target: "",
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
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState("/media");
  const [browserDirs, setBrowserDirs] = useState<{ name: string; path: string }[]>([]);
  const [browserParent, setBrowserParent] = useState("/media");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const wasRunning = useRef(false);

  const refreshAll = useCallback(async () => {
    const [settingsData, targetsData, resultsData, statusData, summaryData] = await Promise.all([
      api.getSettings(),
      api.getTargets(),
      api.getResults(),
      api.getScanStatus(),
      api.getSummary(),
    ]);
    setSettings({ ...DEFAULT_SETTINGS, ...settingsData });
    setTargets(targetsData);
    setResults(resultsData);
    setScanStatus({ ...DEFAULT_SCAN_STATUS, ...statusData });
    setSummary(summaryData.by_target || {});
    setDbLastScan(summaryData.last_scan || null);
    setDbTotalResults(summaryData.total_results || 0);
    setDbTotalErrors(summaryData.total_errors || 0);
  }, []);

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

  async function saveSettings() {
    setSaving(true);
    setMessage("");
    try {
      await api.updateSettings({
        general_discord_webhook: settings.general_discord_webhook,
        failed_discord_webhook: settings.failed_discord_webhook,
        scan_interval_seconds: Number(settings.scan_interval_seconds || "3600"),
        video_extensions: settings.video_extensions,
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

  return (
    <div className="page">
      <header className="header">
        <h1>Video Error Checker</h1>
        <button onClick={runScan} disabled={scanStatus.running}>
          {scanStatus.running ? "Scan Running" : "Run Scan Now"}
        </button>
      </header>

      {scanStatus.running ? (
        <section className="card scan-progress">
          <div className="scan-progress-header">
            <strong>
              {scanStatus.current_target ? `Scanning ${scanStatus.current_target}` : "Scanning"}
            </strong>
            <span>
              {scanStatus.files_total > 0
                ? `${scanStatus.files_done} / ${scanStatus.files_total} files`
                : "Preparing file list..."}
            </span>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <p className="scan-progress-file">{scanStatus.current_file || "Waiting for first file..."}</p>
        </section>
      ) : null}

      {recentLogs.length > 0 ? (
        <section className="card scan-log-panel">
          <div className="results-header">
            <h3>Live Scan Activity</h3>
            <span>{scanStatus.running ? "Live" : "Last run"}</span>
          </div>
          <p className="scan-db-meta">
            DB: {scanStatus.db_target || "unknown"} • Persisted rows: {scanStatus.persisted_results_count}
          </p>
          <div className="scan-log-list">
            {recentLogs.map((entry, index) => (
              <div className="scan-log-row" key={`${entry.timestamp}-${index}`}>
                <span className="scan-log-time">{formatDate(entry.timestamp)}</span>
                <span className={`scan-log-level scan-log-${entry.level}`}>{entry.level}</span>
                <span className="scan-log-message">{entry.message}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="layout">
        <aside className="sidebar card">
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
          <div className="card full">
            <h3>Last Scan</h3>
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
                <th>Duration (s)</th>
                <th>Scanned At</th>
              </tr>
            </thead>
            <tbody>
              {displayedResults.map((result) => (
                <tr key={result.id}>
                  <td>{result.label}</td>
                  <td className="path">{result.file_path}</td>
                  <td className={result.status === "OK" ? "ok" : "error"}>{result.status}</td>
                  <td>{result.scan_duration_seconds.toFixed(2)}</td>
                  <td>{formatDate(result.scanned_at)}</td>
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
