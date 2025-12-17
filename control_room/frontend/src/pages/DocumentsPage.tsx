import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { DocInfo, DocState, ProfilesPayload } from "../types";

const stateLabels: Record<DocState, string> = {
  MISSING: "Manquant",
  ASR_READY: "ASR prêt",
  LEXICON_SUGGESTED: "Lexicon suggéré",
  LEXICON_VALIDATED: "Lexicon validé",
  RAG_READY: "RAG prêt",
  RAG_FAILED: "RAG à corriger",
};

const DocumentsPage = () => {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfilesPayload | null>(null);
  const [lexiconProfile, setLexiconProfile] = useState<string>("default");
  const [ragProfile, setRagProfile] = useState<string>("default");

  const refreshDocs = async () => {
    setLoading(true);
    setMessage(null);
    try {
      setDocs(await api.listDocs());
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const refreshProfiles = async () => {
    try {
      const data = await api.listProfiles();
      setProfiles(data);
      if (!data.profiles.lexicon[lexiconProfile]) {
        setLexiconProfile(Object.keys(data.profiles.lexicon)[0] ?? "default");
      }
      if (!data.profiles.rag[ragProfile]) {
        setRagProfile(Object.keys(data.profiles.rag)[0] ?? "default");
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void refreshDocs();
    void refreshProfiles();
  }, []);

  const trigger = async (
    action: () => Promise<unknown>,
    successMessage: string,
  ) => {
    setMessage(null);
    try {
      await action();
      setMessage(successMessage);
      await refreshDocs();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const copyPath = async (path?: string | null) => {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      setMessage(`Copié: ${path}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section>
      <h2>Documents</h2>
      {message && <div className="card">{message}</div>}
      <div className="card">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "1rem",
            flexWrap: "wrap",
          }}
        >
          <div>
            <h3>Staging ASR</h3>
            {profiles && (
              <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
                <label>
                  Profil lexicon
                  <select
                    value={lexiconProfile}
                    onChange={(e) => setLexiconProfile(e.target.value)}
                  >
                    {Object.entries(profiles.profiles.lexicon).map(([key, entry]) => (
                      <option key={key} value={key}>
                        {entry.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Profil RAG
                  <select
                    value={ragProfile}
                    onChange={(e) => setRagProfile(e.target.value)}
                  >
                    {Object.entries(profiles.profiles.rag).map(([key, entry]) => (
                      <option key={key} value={key}>
                        {entry.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </div>
          <button className="secondary" onClick={refreshDocs} disabled={loading}>
            Rafraîchir
          </button>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Document</th>
              <th>État</th>
              <th>Suggested</th>
              <th>Dernier RAG</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((doc) => (
              <tr key={doc.name}>
                <td>
                  <strong>{doc.name}</strong>
                  {doc.locked && (
                    <span className="status-badge danger" style={{ marginLeft: "0.5rem" }}>
                      Verrouillé
                    </span>
                  )}
                </td>
                <td>
                  <span className="status-badge">
                    {stateLabels[doc.doc_state] ?? doc.doc_state}
                  </span>
                </td>
                <td>{doc.suggested_count}</td>
                <td>{doc.last_rag_version ?? "—"}</td>
                <td>
                  <div className="button-row">
                    <Link to={`/docs/${encodeURIComponent(doc.name)}`}>
                      <button className="secondary">Inspecter</button>
                    </Link>
                    {doc.allowed_actions.includes("lexicon_scan") && (
                      <button
                        onClick={() =>
                          trigger(
                            () =>
                              api.runLexiconScan({
                                doc: doc.name,
                                profile: lexiconProfile,
                              }),
                            `Scan lancé pour ${doc.name}`,
                          )
                        }
                        disabled={loading}
                      >
                        Scan
                      </button>
                    )}
                    {doc.allowed_actions.includes("lexicon_apply") && (
                      <button
                        onClick={() =>
                          trigger(
                            () =>
                              api.runLexiconApply({
                                doc: doc.name,
                                profile: lexiconProfile,
                              }),
                            `Apply lancé pour ${doc.name}`,
                          )
                        }
                        disabled={loading}
                      >
                        Apply
                      </button>
                    )}
                    {doc.allowed_actions.includes("rag_export") && (
                      <button
                        onClick={() =>
                          trigger(
                            () =>
                              api.runRagExport({
                                doc: doc.name,
                                force: true,
                                profile: ragProfile,
                              }),
                            `RAG export lancé pour ${doc.name}`,
                          )
                        }
                        disabled={loading}
                      >
                        RAG
                      </button>
                    )}
                    {doc.allowed_actions.includes("rag_doctor") && (
                      <button
                        className="secondary"
                        onClick={() =>
                          trigger(
                            () =>
                              api.runRagDoctor({
                                doc: doc.name,
                                profile: ragProfile,
                              }),
                            `RAG doctor lancé pour ${doc.name}`,
                          )
                        }
                        disabled={loading}
                      >
                        Doctor
                      </button>
                    )}
                    {doc.allowed_actions.includes("rag_query") && (
                      <button
                        className="secondary"
                        onClick={() =>
                          trigger(
                            () =>
                              api.runRagQuery({
                                doc: doc.name,
                                query: "healthcheck",
                                profile: ragProfile,
                              }),
                            `RAG query lancé pour ${doc.name}`,
                          )
                        }
                        disabled={loading}
                      >
                        Query test
                      </button>
                    )}
                    <button
                      className="secondary"
                      onClick={() => copyPath(doc.work_dir)}
                      disabled={!doc.work_dir}
                    >
                      Copier chemin
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default DocumentsPage;
