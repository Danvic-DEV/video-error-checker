export type Settings = {
  general_discord_webhook: string;
  failed_discord_webhook: string;
  scan_interval_seconds: string;
  video_extensions: string;
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
