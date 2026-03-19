import type {
  GpuDiagnostics,
  GpuDiscovery,
  ResultRow,
  ScanStatus,
  Settings,
  Target,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getSettings: () => request<Settings>("/api/settings"),
  updateSettings: (payload: {
    general_discord_webhook: string;
    failed_discord_webhook: string;
    scan_interval_seconds: number;
    video_extensions: string;
    gpu_enabled: boolean;
    gpu_backend: string;
    gpu_device_id: string;
  }) =>
    request<{ status: string }>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getGpuDiscovery: () => request<GpuDiscovery>("/api/gpu/discovery"),
  getGpuDiagnostics: () => request<GpuDiagnostics>("/api/gpu/diagnostics"),

  getTargets: () => request<Target[]>("/api/targets"),
  browseTargets: (path?: string) =>
    request<{ path: string; parent: string; directories: { name: string; path: string }[] }>(
      `/api/targets/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`
    ),
  createTarget: (payload: { label: string; path: string; enabled: boolean }) =>
    request<{ id: number }>("/api/targets", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTarget: (id: number, payload: { label: string; path: string; enabled: boolean }) =>
    request<{ status: string }>(`/api/targets/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteTarget: (id: number) =>
    request<{ status: string }>(`/api/targets/${id}`, { method: "DELETE" }),

  getResults: () => request<ResultRow[]>("/api/results"),
  rescanResult: (id: number) =>
    request<{
      id: number;
      status: string;
      details: string;
      scan_duration_seconds: number;
      scanned_at: string;
    }>(`/api/results/${id}/rescan`, { method: "POST" }),
  getSummary: () =>
    request<{
      by_target: Record<string, Record<string, number>>;
      last_scan: string | null;
      total_results: number;
      total_errors: number;
    }>("/api/results/summary"),

  triggerScan: () => request<{ status: string }>("/api/scan/trigger", { method: "POST" }),
  getScanStatus: () => request<ScanStatus>("/api/scan/status"),
};
