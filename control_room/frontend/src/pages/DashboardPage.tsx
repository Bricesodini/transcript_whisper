import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import { api } from "../api/client";
import { DocInfo, DocState, JobRecord } from "../types";

const DashboardPage = () => {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [docData, jobData] = await Promise.all([api.listDocs(), api.listJobs(10)]);
      setDocs(docData);
      setJobs(jobData);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const stats = useMemo(() => {
    const total = docs.length;
    const perState: Partial<Record<DocState, number>> = {};
    docs.forEach((doc) => {
      perState[doc.doc_state] = (perState[doc.doc_state] ?? 0) + 1;
    });
    return { total, perState };
  }, [docs]);

  const handleLexiconScan = async () => {
    setError(null);
    try {
      await api.runLexiconBatch({ scan_only: true });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleLexiconApply = async () => {
    setError(null);
    try {
      await api.runLexiconBatch({ apply: true });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleAsrBatch = async () => {
    setError(null);
    try {
      await api.runAsrBatch({});
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section>
      <h2>Dashboard</h2>
      {error && <div className="card danger">{error}</div>}
      <div className="grid two">
        <div className="card">
          <h3>Documents</h3>
          <p>Total : {stats.total}</p>
          <ul>
            {Object.entries(stats.perState).map(([state, count]) => (
              <li key={state}>
                {state} : {count}
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h3>Actions rapides</h3>
          <div className="button-row">
            <button onClick={handleLexiconScan} disabled={loading}>
              Lancer lexicon scan
            </button>
            <button onClick={handleLexiconApply} disabled={loading}>
              Lancer lexicon apply
            </button>
            <button onClick={handleAsrBatch} disabled={loading}>
              Lancer ASR batch
            </button>
          </div>
        </div>
      </div>

      <div className="card">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <h3>Derniers jobs</h3>
          <button className="secondary" onClick={refresh} disabled={loading}>
            Rafraîchir
          </button>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Action</th>
              <th>Statut</th>
              <th>Doc</th>
              <th>Début</th>
              <th>Durée (s)</th>
              <th>Raison</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.id}</td>
                <td>{job.action}</td>
                <td>
                  <span className={`status-badge status-${job.status}`}>{job.status}</span>
                </td>
                <td>{job.doc_id || "—"}</td>
                <td>{job.started_at ? dayjs(job.started_at).format("DD/MM HH:mm") : "—"}</td>
                <td>{job.duration_ms ? (job.duration_ms / 1000).toFixed(1) : "—"}</td>
                <td>
                  {job.failure_type && job.failure_type !== "none"
                    ? `${job.failure_type}${job.failure_hint ? ` (${job.failure_hint})` : ""}`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default DashboardPage;
