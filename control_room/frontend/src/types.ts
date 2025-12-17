export interface ApiError {
  code: string;
  message: string;
  hint?: string | null;
}

export interface ApiEnvelope<T> {
  api_version: string;
  data: T;
  error: ApiError | null;
}

export interface StampInfo {
  doc?: string | null;
  source_file?: string | null;
  source_sha256?: string | null;
  rules_count?: number | null;
  updated_at_utc?: string | null;
}

export type DocState =
  | "MISSING"
  | "ASR_READY"
  | "LEXICON_SUGGESTED"
  | "LEXICON_VALIDATED"
  | "RAG_READY"
  | "RAG_FAILED";

export interface JobSummary {
  id: number;
  action: string;
  status: JobStatus;
  failure_type: string;
  failure_hint?: string | null;
  created_at: string;
  ended_at?: string | null;
}

export interface DocInfo {
  name: string;
  work_dir?: string | null;
  transcript_dir?: string | null;
  suggested_path?: string | null;
  validated_path?: string | null;
  has_suggested: boolean;
  has_validated: boolean;
  has_stamp: boolean;
  suggested_count: number;
  stamp?: StampInfo | null;
  doc_state: DocState;
  rag_versions: string[];
  last_rag_version?: string | null;
  rag_ready: boolean;
  last_job?: JobSummary | null;
  last_rag_job?: JobSummary | null;
  allowed_actions: string[];
  locked: boolean;
  locked_by_job_id?: number | null;
  suggested_etag?: string | null;
}

export type JobStatus = "queued" | "running" | "success" | "fail" | "canceled";

export interface JobRecord {
  id: number;
  action: string;
  status: JobStatus;
  argv: string[];
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  log_path?: string | null;
  doc_id?: string | null;
  job_version: number;
  failure_type: string;
  failure_hint?: string | null;
  write_lock?: boolean;
  profile_id?: string | null;
  artifacts: string[];
  duration_ms?: number | null;
}

export interface GlossaryRule {
  pattern?: string;
  replacement?: string;
  confidence?: number;
  evidence?: string[];
  [key: string]: unknown;
}

export interface PreviewResult {
  source: string;
  preview: string;
  diff?: string;
  count?: number;
  error?: string;
}

export interface ProfileEntry {
  key: string;
  label: string;
  args: string[];
}

export interface ProfilesPayload {
  profiles: {
    version: number;
    asr: Record<string, ProfileEntry>;
    lexicon: Record<string, ProfileEntry>;
    rag: Record<string, ProfileEntry>;
  };
}
