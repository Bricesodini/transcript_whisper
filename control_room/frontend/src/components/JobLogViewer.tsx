import { useEffect, useState } from "react";
import { api } from "../api/client";

const WS_BASE = import.meta.env.VITE_WS_BASE;
const API_KEY = import.meta.env.VITE_API_KEY;

interface Props {
  jobId: number | null;
}

const JobLogViewer = ({ jobId }: Props) => {
  const [log, setLog] = useState("");

  useEffect(() => {
    setLog("");
    if (!jobId) {
      return;
    }
    let ws: WebSocket | null = null;
    let isMounted = true;

    const openSocket = () => {
      if (!jobId) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = WS_BASE ?? window.location.host;
      const url = `${protocol}//${host}/ws/jobs/${jobId}`;
      const protocols = API_KEY ? [API_KEY] : undefined;
      ws = new WebSocket(url, protocols);
      ws.onmessage = (event) => {
        if (!isMounted) return;
        setLog((prev) => (prev ? `${prev}\n${event.data}` : event.data));
      };
    };

    api
      .getJobLog(jobId)
      .then((text) => {
        if (!isMounted) return;
        setLog(text);
      })
      .catch(() => {
        /* ignore */
      })
      .finally(() => openSocket());

    return () => {
      isMounted = false;
      if (ws) {
        ws.close();
      }
    };
  }, [jobId]);

  if (!jobId) {
    return <div className="log-viewer">Selectionnez un job</div>;
  }

  return <div className="log-viewer">{log || "Chargement des logs..."}</div>;
};

export default JobLogViewer;
