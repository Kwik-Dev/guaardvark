// The search reranker (Settings > Knowledge): whether it is installed, and the
// Install that is the only way its weights reach this machine. Searches run
// without reranking until it is installed; they never download it themselves.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Line, Hint, StatusPill, ActionButton } from "./ui";
import { getRerankerStatus, installReranker } from "../../api/settingsService";

const RerankerControl = ({ showMessage }) => {
  const [status, setStatus] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getRerankerStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    load();
    return () => clearTimeout(timer.current);
  }, [load]);

  const running = status?.install?.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    timer.current = setTimeout(load, 2000);
    return () => clearTimeout(timer.current);
  }, [running, status, load]);

  const install = async () => {
    try {
      setStatus(await installReranker());
    } catch (e) {
      showMessage?.(`Could not start the install: ${e.message || e}`, "error");
    }
  };

  if (!status) return null;
  const size = status.size_gb ? `${status.size_gb} GB` : "";
  const job = status.install || {};

  if (!status.enabled) {
    return (
      <Line>
        <StatusPill label="Off" tooltip="GUAARDVARK_RERANK_CROSS_ENCODER is false in .env" />
      </Line>
    );
  }
  if (status.installed) {
    return (
      <Line>
        <StatusPill label="Installed" tone="ok" tooltip={status.model} />
        {status.load_error && <Hint>Did not load: {status.load_error}</Hint>}
      </Line>
    );
  }
  return (
    <Line>
      {running ? (
        <StatusPill
          label={job.progress != null ? `Installing ${job.progress}%` : `Installing ${job.downloaded_gb} GB`}
          tone="info"
          tooltip={status.model}
        />
      ) : (
        <StatusPill label="Not installed" tone="warn" tooltip={status.model} />
      )}
      {!running && (
        <ActionButton
          onClick={install}
          tooltip={`Downloads ${status.model}${size ? ` (${size})` : ""} from Hugging Face. Searches work without it, ranked less precisely.`}
        >
          {size ? `Install (${size})` : "Install"}
        </ActionButton>
      )}
      {job.state === "failed" && <Hint>Install failed: {job.error}</Hint>}
    </Line>
  );
};

export default RerankerControl;
