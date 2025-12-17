import {
  DocInfo,
  GlossaryRule,
  JobRecord,
  PreviewResult,
  ProfilesPayload,
  StoragePayload,
} from "../types";
import type { ApiEnvelope } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";
const API_KEY = import.meta.env.VITE_API_KEY;

type Json = Record<string, unknown>;

async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (API_KEY) {
    headers["X-API-KEY"] = API_KEY;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  if (response.status === 204) {
    if (!response.ok) {
      throw new Error(response.statusText);
    }
    return null as T;
  }
  const payload = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !payload) {
    const text = payload ? JSON.stringify(payload) : await response.text();
    throw new Error(text || response.statusText);
  }
  if (payload.error) {
    throw new Error(payload.error.hint ?? payload.error.message);
  }
  if (payload.api_version !== "v1") {
    throw new Error("API version mismatch");
  }
  return payload.data;
}

export const api = {
  async listDocs(): Promise<DocInfo[]> {
    const res = await apiRequest<{ docs: DocInfo[] }>("/docs");
    return res.docs;
  },
  async getDoc(name: string): Promise<DocInfo> {
    const res = await apiRequest<{ doc: DocInfo }>(
      `/docs/${encodeURIComponent(name)}`,
    );
    return res.doc;
  },
  async getSuggested(name: string): Promise<{ rules: GlossaryRule[]; etag?: string }> {
    const res = await apiRequest<{ rules: GlossaryRule[]; etag?: string }>(
      `/docs/${encodeURIComponent(name)}/suggested`,
    );
    return { rules: res.rules ?? [], etag: res.etag };
  },
  async saveValidated(name: string, rules: GlossaryRule[], etag?: string) {
    return apiRequest(`/docs/${encodeURIComponent(name)}/validated`, {
      method: "PUT",
      body: JSON.stringify({ rules, doc_id: name, etag }),
    });
  },
  async previewDoc(name: string, params: { pattern?: string; replacement?: string }) {
    const query = new URLSearchParams();
    if (params.pattern) query.set("pattern", params.pattern);
    if (params.replacement) query.set("replacement", params.replacement);
    const res = await apiRequest<{ preview: PreviewResult }>(
      `/docs/${encodeURIComponent(name)}/preview?${query.toString()}`,
    );
    return res.preview;
  },
  async listProfiles(): Promise<ProfilesPayload> {
    return apiRequest<ProfilesPayload>("/profiles");
  },
  async runAsrBatch(payload: Json = {}) {
    const res = await apiRequest<{ job: JobRecord }>("/run/asr-batch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runLexiconBatch(payload: Json = {}) {
    const res = await apiRequest<{ job: JobRecord }>("/run/lexicon-batch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runRagBatch(payload: Json = {}) {
    const res = await apiRequest<{ job: JobRecord }>("/run/rag-batch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runLexiconScan(payload: Json) {
    const res = await apiRequest<{ job: JobRecord }>("/run/lexicon-scan", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runLexiconApply(payload: Json) {
    const res = await apiRequest<{ job: JobRecord }>("/run/lexicon-apply", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runRagExport(payload: Json) {
    const res = await apiRequest<{ job: JobRecord }>("/run/rag-export", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runRagDoctor(payload: Json) {
    const res = await apiRequest<{ job: JobRecord }>("/run/rag-doctor", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async runRagQuery(payload: Json) {
    const res = await apiRequest<{ job: JobRecord }>("/run/rag-query", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return res.job;
  },
  async listJobs(limit = 100): Promise<JobRecord[]> {
    const res = await apiRequest<{ jobs: JobRecord[] }>(`/jobs?limit=${limit}`);
    return res.jobs;
  },
  async getJob(jobId: number): Promise<JobRecord> {
    const res = await apiRequest<{ job: JobRecord }>(`/jobs/${jobId}`);
    return res.job;
  },
  async getJobLog(jobId: number) {
    const res = await apiRequest<{ log: string }>(`/jobs/${jobId}/log`);
    return res.log;
  },
  async cancelJob(jobId: number) {
    return apiRequest(`/jobs/${jobId}/cancel`, { method: "POST" });
  },
  async getStorage(): Promise<StoragePayload> {
    return apiRequest<StoragePayload>("/storage");
  },
};
