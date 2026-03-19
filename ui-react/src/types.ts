export type Settings = {
  general_discord_webhook: string;
  failed_discord_webhook: string;
  scan_interval_seconds: string;
  video_extensions: string;
  gpu_enabled: boolean;
  gpu_backend: string;
  gpu_device_id: string;
};

export type GpuBackendOption = {
  id: string;
  label: string;
  available: boolean;
  reason: string;
};

export type GpuDeviceOption = {
  id: string;
  label: string;
  backend: string;
  usable: boolean;
  reason: string;
};

export type GpuDiscovery = {
  hwaccels: string[];
  backends: GpuBackendOption[];
  devices: GpuDeviceOption[];
  warnings: string[];
};

export type GpuCheck = {
  id: string;
  severity: string;
  ok: boolean;
  message: string;
  hint: string;
};

export type GpuProbe = {
  backend: string;
  device_id: string;
  ok: boolean;
  message: string;
  hint: string;
};

export type GpuDiagnostics = {
  summary: {
    gpu_enabled: boolean;
    selected_backend: string;
    resolved_backend: string;
    selected_device: string;
    healthy: boolean;
  };
  environment: {
    NVIDIA_VISIBLE_DEVICES: string;
    NVIDIA_DRIVER_CAPABILITIES: string;
  };
  checks: GpuCheck[];
  probes: GpuProbe[];
};

export type Target = {
  id: number;
  label: string;
  path: string;
  enabled: boolean;
};

export type ResultRow = {
  id: number;
  label: string;
  file_path: string;
  last_modified: number;
  status: string;
  details: string;
  scan_duration_seconds: number;
  scanned_at: string;
};

export type ScanLogEntry = {
  timestamp: string;
  level: string;
  message: string;
  source?: string;
};

export type ScanStatus = {
  running: boolean;
  last_started: string | null;
  last_completed: string | null;
  last_summary: Record<string, number>;
  files_total: number;
  files_done: number;
  current_file: string;
  current_file_path: string;
  current_file_started_at: string | null;
  current_file_elapsed_seconds: number;
  current_target: string;
  recent_logs: ScanLogEntry[];
  persisted_results_count: number;
  db_target: string;
  active_rescan: {
    result_id: number | null;
    file_path: string;
    started_at: string | null;
    elapsed_seconds: number;
  };
  queued_rescans: { result_id: number; file_path: string }[];
};
