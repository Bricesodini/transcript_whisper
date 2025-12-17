import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import { api } from "../api/client";
import { JobRecord } from "../types";
import JobLogViewer from "../components/JobLogViewer";

const JobsPage = () => {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [filterDoc, setFilterDoc] = useState<string>("");
  const [filterType, setFilterType] = useState<string>("");

  const refresh = async () => {
    setLoading(true);
    setMessage(null);
    try {
      const data = await api.listJobs(50);
      setJobs(data);
      if (!selectedJobId && data.length > 0) {
        setSelectedJobId(data[0].id);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (filterDoc && job.doc_id && !job.doc_id.toLowerCase().includes(filterDoc.toLowerCase())) {
        return false;
      }
      if (filterType && !job.action.toLowerCase().includes(filterType.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [jobs, filterDoc, filterType]);

  const cancelJob = async (jobId: number) => {
    try {
      await api.cancelJob(jobId);
      setMessage(`Job ${jobId} annulé`);
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section>
      <h2>Jobs</h2>
      {message && <div className="card">{message}</div>}
      <div className="card" style={{ marginBottom: "1rem" }}>
        <div className="grid two">
          <label>
            Filtrer doc
            <input value={filterDoc} onChange={(e) => setFilterDoc(e.target.value)} placeholder="Doc ID" />
          </label>
          <label>
            Filtrer type
            <input value={filterType} onChange={(e) => setFilterType(e.target.value)} placeholder="run_..." />
          </label>
        </div>
      </div>
      <div className="grid two" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <div className="card">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <h3>Historique</h3>
            <button className="secondary" onClick={refresh} disabled={loading}>
              Rafraîchir
            </button>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Action</th>
                <th>Doc</th>
                <th>Statut</th>
                <th>Début</th>
                <th>Durée</th>
                <th>Raison</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map((job) => (
                <tr
                  key={job.id}
                  onClick={() => setSelectedJobId(job.id)}
                  style={{
                    cursor: "pointer",
                    background: selectedJobId === job.id ? "#e0f2fe" : undefined,
                  }}
                >
                  <td>{job.id}</td>
                  <td>{job.action}</td>
                  <td>{job.doc_id ?? "—"}</td>
                  <td>
                    <span className={`status-badge status-${job.status}`}>{job.status}</span>
                  </td>
                  <td>{job.started_at ? dayjs(job.started_at).format("DD/MM HH:mm") : "—"}</td>
                  <td>{job.duration_ms ? `${(job.duration_ms / 1000).toFixed(1)}s` : "—"}</td>
                  <td>
                    {job.failure_type && job.failure_type !== "none"
                      ? `${job.failure_type}${job.failure_hint ? ` (${job.failure_hint})` : ""}`
                      : "—"}
                  </td>
                  <td>
                    {job.status === "queued" || job.status === "running" ? (
                      <button
                        className="danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          void cancelJob(job.id);
                        }}
                      >
                        Cancel
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Logs</h3>
          <JobLogViewer jobId={selectedJobId} />
        </div>
      </div>
    </section>
  );
};

export default JobsPage;
