import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StorageDirInfo, StoragePayload } from "../types";

const formatSize = (bytes: number) => {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** idx;
  return `${value.toFixed(2)} ${units[idx]}`;
};

const StoragePage = () => {
  const [data, setData] = useState<StoragePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let mounted = true;
    api
      .getStorage()
      .then((payload) => {
        if (mounted) {
          setData(payload);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  const renderDirs = (dirs: StorageDirInfo[]) => (
    <table>
      <thead>
        <tr>
          <th>Zone</th>
          <th>Chemin</th>
          <th>Taille</th>
          <th>Fichiers</th>
          <th>Oldest</th>
          <th>Newest</th>
        </tr>
      </thead>
      <tbody>
        {dirs.map((dir) => (
          <tr key={dir.path}>
            <td>{dir.label}</td>
            <td>{dir.path}</td>
            <td>{formatSize(dir.size_bytes)}</td>
            <td>{dir.items}</td>
            <td>{dir.oldest ?? "—"}</td>
            <td>{dir.newest ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  if (loading) {
    return <p>Chargement…</p>;
  }
  if (error) {
    return <p className="error">{error}</p>;
  }
  if (!data) {
    return <p>Aucune donnée.</p>;
  }

  return (
    <section>
      <h2>Storage & Cleanup</h2>
      <p>Racine NAS : {data.root}</p>
      <div className="panel">{renderDirs(data.directories)}</div>
      <div className="panel">
        <h3>Docs les plus volumineux (ASR staging)</h3>
        {data.heavy_docs.length === 0 ? (
          <p>Aucun document détecté.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Doc</th>
                <th>Taille</th>
                <th>Emplacement</th>
              </tr>
            </thead>
            <tbody>
              {data.heavy_docs.map((doc) => (
                <tr key={doc.doc_id}>
                  <td>{doc.doc_id}</td>
                  <td>{formatSize(doc.size_bytes)}</td>
                  <td>{doc.location}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h3>Orphelins</h3>
        <p>
          <strong>RAG sans source :</strong>{" "}
          {data.orphans.missing_source.length ? data.orphans.missing_source.join(", ") : "Aucun"}
        </p>
        <p>
          <strong>Sources sans RAG :</strong>{" "}
          {data.orphans.missing_rag.length ? data.orphans.missing_rag.join(", ") : "Aucun"}
        </p>
      </div>
    </section>
  );
};

export default StoragePage;
