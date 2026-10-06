export interface Settings {
  source_owner: string; source_id: string; source_path: string; recursive: boolean; include: string; exclude: string;
  archive_path: string; archive_id: string;
  debounce_seconds: number; reconciliation_minutes: number; reconcile_on_start: boolean; check_month: boolean;
  headers: Record<string, string>; duration_minutes: number; timezone: string; missing_file_policy: string;
  calendar_owner: string; internal_calendar: string; public_enabled: boolean; public_calendar: string; public_link: string;
  smtp_enabled: boolean; smtp_mode: 'custom' | 'nextcloud'; smtp_host: string; smtp_port: number; smtp_security: string; smtp_user: string; smtp_sender: string; smtp_name: string;
  overdue_minutes: number; notify_changes: boolean; notify_cancellation: boolean; retention_days: number;
  delete_guard: boolean; delete_percent: number; delete_minimum: number; smtp_password_set?: boolean;
}
export interface Issue { level: string; message: string; file?: string; sheet?: string; row?: number }
export interface Operation { target: string; action: string; source_key: string; file_id: string; sheet: string; reason: string }
export interface Run {
  id: string; status: string; started_at: string; files_scanned: number;
  result: null | { message?: string; internal?: Record<string, number>; public?: Record<string, number>; email_jobs?: number; email_skipped?: number; issues?: Issue[]; operations?: Operation[] }
}
export interface Status {
  enabled: boolean; version: string; source_path: string; last_files_event: string; last_full_reconciliation: string; last_successful_sync: string;
  counts: { files: number; events: number; public: number; email: {status: string; n: number}[]; logs: {level: string; n: number}[]; issues: number }; runs: Run[];
  issues: Issue[];
  recent_emails: { id: number; recipient: string; scheduled_at: string; status: string; last_error?: string; sent_at?: string; kind: string }[];
}
export interface CalendarOption { name: string; url: string; writable: boolean }
export interface SourceFile { file_id: string; path: string; status: string; last_sync_at: string; last_error: string }
export interface Log { id: number; at: string; level: string; subsystem: string; file: string; sheet: string; operation: string; result: string }
export interface Preview { file: string; year: number; sheets: { name: string; date?: string; headers?: Record<string, number> }[]; issues: Issue[]; events: { title: string; start: string; end: string; location: string; responsible: string }[] }
