import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DocInfo, DocState, GlossaryRule, PreviewResult, ProfilesPayload } from "../types";

const stateLabels: Record<DocState, string> = {
  MISSING: "Manquant",
  ASR_READY: "ASR prêt",
  LEXICON_SUGGESTED: "Lexicon suggéré",
  LEXICON_VALIDATED: "Lexicon validé",
  RAG_READY: "RAG prêt",
  RAG_FAILED: "RAG à corriger",
};

interface RuleForm extends GlossaryRule {
  enabled?: boolean;
}

type TabKey = "sources" | "glossary" | "preview" | "runs";

const DocumentDetailPage = () => {
  const { docName } = useParams();
  const docId = docName ? decodeURIComponent(docName) : "";
  const [doc, setDoc] = useState<DocInfo | null>(null);
  const [rules, setRules] = useState<RuleForm[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [previewPattern, setPreviewPattern] = useState("");
  const [previewReplacement, setPreviewReplacement] = useState("");
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const [suggestedEtag, setSuggestedEtag] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfilesPayload | null>(null);
  const [lexiconProfile, setLexiconProfile] = useState<string>("default");
  const [ragProfile, setRagProfile] = useState<string>("default");
  const [activeTab, setActiveTab] = useState<TabKey>("glossary");
  const [ragQuery, setRagQuery] = useState("");

  const refreshDoc = async () => {
    if (!docId) return;
    setLoading(true);
    setMessage(null);
    try {
      const [docInfo, suggestedRes] = await Promise.all([
        api.getDoc(docId),
        api.getSuggested(docId),
      ]);
      setDoc(docInfo);
      setRules(
        suggestedRes.rules.map((rule) => ({
          ...rule,
          enabled: true,
        })),
      );
      setSuggestedEtag(suggestedRes.etag ?? docInfo.suggested_etag ?? null);
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
    void refreshDoc();
    void refreshProfiles();
  }, [docId]);

  const enabledRules = useMemo(
    () => rules.filter((rule) => rule.enabled !== false),
    [rules],
  );

  const updateRule = (index: number, field: keyof RuleForm, value: unknown) => {
    setRules((prev) =>
      prev.map((rule, idx) =>
        idx === index
          ? {
              ...rule,
              [field]: value,
            }
          : rule,
      ),
    );
  };

  const saveValidated = async () => {
    if (!docId) return;
    setMessage(null);
    try {
      await api.saveValidated(docId, enabledRules, suggestedEtag ?? doc?.suggested_etag ?? undefined);
      setMessage("Glossaire validé sauvegardé");
      await refreshDoc();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const preview = async () => {
    if (!docId) return;
    try {
      setPreviewResult(
        await api.previewDoc(docId, {
          pattern: previewPattern,
          replacement: previewReplacement,
        }),
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const runAction = async (action: () => Promise<unknown>, success: string) => {
    setMessage(null);
    try {
      await action();
      setMessage(success);
      await refreshDoc();
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

  if (!docId) {
    return <p>Document invalide.</p>;
  }

  const stateLabel = doc ? stateLabels[doc.doc_state] ?? doc.doc_state : "";

  return (
    <section>
      <header style={{ display: "flex", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
        <div>
          <h2>{docId}</h2>
          <Link to="/docs">&larr; Retour</Link>
          {doc && (
            <p>
              <span className="status-badge">{stateLabel}</span>{" "}
              {doc.last_rag_version ? `Dernier RAG: ${doc.last_rag_version}` : null}
              {doc.locked && (
                <span className="status-badge danger" style={{ marginLeft: "0.5rem" }}>
                  Verrouillé
                </span>
              )}
            </p>
          )}
        </div>
        <div className="button-row">
          <button onClick={saveValidated} disabled={loading}>
            Sauvegarder
          </button>
          {doc?.allowed_actions.includes("lexicon_apply") && (
            <button
              onClick={() =>
                runAction(
                  () =>
                    api.runLexiconApply({
                      doc: docId,
                      profile: lexiconProfile,
                    }),
                  "Apply lancé",
                )
              }
              disabled={loading}
            >
              Lancer apply
            </button>
          )}
          {doc?.allowed_actions.includes("rag_export") && (
            <button
              onClick={() =>
                runAction(
                  () =>
                    api.runRagExport({
                      doc: docId,
                      force: true,
                      profile: ragProfile,
                    }),
                  "RAG export lancé",
                )
              }
              disabled={loading}
            >
              RAG export
            </button>
          )}
          <button className="secondary" onClick={refreshDoc} disabled={loading}>
            Rafraîchir
          </button>
        </div>
      </header>
      {message && <div className="card">{message}</div>}

      {profiles && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
            <label>
              Profil lexicon
              <select value={lexiconProfile} onChange={(e) => setLexiconProfile(e.target.value)}>
                {Object.entries(profiles.profiles.lexicon).map(([key, entry]) => (
                  <option key={key} value={key}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Profil RAG
              <select value={ragProfile} onChange={(e) => setRagProfile(e.target.value)}>
                {Object.entries(profiles.profiles.rag).map(([key, entry]) => (
                  <option key={key} value={key}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}

      <nav className="tabs">
        {(["sources", "glossary", "preview", "runs"] as TabKey[]).map((tab) => (
          <button
            key={tab}
            className={tab === activeTab ? "active" : ""}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "sources" && "Sources"}
            {tab === "glossary" && "Glossaire"}
            {tab === "preview" && "Preview"}
            {tab === "runs" && "Runs"}
          </button>
        ))}
      </nav>

      {activeTab === "sources" && doc && (
        <div className="card">
          <h3>Sources</h3>
          <ul>
            <li>
              Work dir: {doc.work_dir ?? "—"}{" "}
              <button className="secondary" onClick={() => copyPath(doc.work_dir)} disabled={!doc.work_dir}>
                Copier
              </button>
            </li>
            <li>
              Transcript: {doc.transcript_dir ?? "—"}{" "}
              <button
                className="secondary"
                onClick={() => copyPath(doc.transcript_dir)}
                disabled={!doc.transcript_dir}
              >
                Copier
              </button>
            </li>
            <li>Suggested: {doc.suggested_path ?? "—"}</li>
            <li>Validated: {doc.validated_path ?? "—"}</li>
            <li>RAG versions: {doc.rag_versions.join(", ") || "—"}</li>
          </ul>
        </div>
      )}

      {activeTab === "glossary" && (
        <div className="card">
          <h3>Suggestions</h3>
          <div className="button-row">
            <button
              className="secondary"
              onClick={() =>
                setRules((prev) => [...prev, { pattern: "", replacement: "", enabled: true }])
              }
            >
              Ajouter une règle
            </button>
          </div>
          <div className="grid">
            {rules.map((rule, idx) => (
              <div key={idx} className="card" style={{ background: "#f8fafc" }}>
                <label>
                  <strong>Pattern</strong>
                  <input
                    value={rule.pattern ?? ""}
                    onChange={(e) => updateRule(idx, "pattern", e.target.value)}
                  />
                </label>
                <label>
                  <strong>Replacement</strong>
                  <input
                    value={rule.replacement ?? ""}
                    onChange={(e) => updateRule(idx, "replacement", e.target.value)}
                  />
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={rule.enabled !== false}
                    onChange={(e) => updateRule(idx, "enabled", e.target.checked)}
                  />{" "}
                  Inclure cette règle
                </label>
                {rule.evidence && (
                  <p style={{ fontSize: "0.85rem" }}>
                    <strong>Evidence:</strong> {rule.evidence.join(" / ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === "preview" && (
        <div className="card">
          <h3>Preview</h3>
          <div className="grid two">
            <label>
              Pattern
              <input
                value={previewPattern}
                onChange={(e) => setPreviewPattern(e.target.value)}
                placeholder="Regex"
              />
            </label>
            <label>
              Replacement
              <input
                value={previewReplacement}
                onChange={(e) => setPreviewReplacement(e.target.value)}
                placeholder="Valeur"
              />
            </label>
          </div>
          <button className="secondary" onClick={preview}>
            Générer preview
          </button>
          {previewResult && (
            <div className="grid two">
              <div>
                <h4>Source</h4>
                <pre className="log-viewer">{previewResult.source}</pre>
              </div>
              <div>
                <h4>Preview</h4>
                <pre className="log-viewer">{previewResult.preview}</pre>
                {previewResult.diff && (
                  <details>
                    <summary>Diff (count: {previewResult.count ?? 0})</summary>
                    <pre className="log-viewer">{previewResult.diff}</pre>
                  </details>
                )}
                {previewResult.error && (
                  <p style={{ color: "#b91c1c" }}>{previewResult.error}</p>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "runs" && (
        <div className="card">
          <h3>Historique</h3>
          {doc?.last_job ? (
            <div className="card">
              <p>
                <strong>Dernier job:</strong> #{doc.last_job.id} {doc.last_job.action} -{" "}
                {doc.last_job.status}
              </p>
              {doc.last_job.failure_type !== "none" && (
                <p>
                  Failure: {doc.last_job.failure_type} {doc.last_job.failure_hint ?? ""}
                </p>
              )}
            </div>
          ) : (
            <p>Aucun job enregistré.</p>
          )}
          {doc?.allowed_actions.includes("rag_doctor") && (
            <div style={{ marginTop: "1rem" }}>
              <button
                className="secondary"
                onClick={() =>
                  runAction(
                    () =>
                      api.runRagDoctor({
                        doc: docId,
                        profile: ragProfile,
                      }),
                    "rag doctor lancé",
                  )
                }
              >
                Lancer rag doctor
              </button>
            </div>
          )}
          <div style={{ marginTop: "1rem" }}>
            <label>
              Query RAG
              <input value={ragQuery} onChange={(e) => setRagQuery(e.target.value)} />
            </label>
            {doc?.allowed_actions.includes("rag_query") && (
              <button
                className="secondary"
                onClick={() =>
                  runAction(
                    () =>
                      api.runRagQuery({
                        doc: docId,
                        query: ragQuery,
                        profile: ragProfile,
                      }),
                    "rag query lancé",
                  )
                }
                disabled={!ragQuery}
              >
                Lancer rag query
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
};

export default DocumentDetailPage;
