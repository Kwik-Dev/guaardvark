// The image encoder behind the Cast identity check (Settings > Generation):
// whether it is installed, which check each place gets, and the Install that is
// the only way its weights reach this machine. Without it the post-train smoke
// still reads "Identity: not measured" and clips keep the colour match.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActionButton, ConfirmActionDialog, Hint, Line, StatusPill } from "./ui";
import {
  getIdentityCheck,
  installIdentityEncoder,
  removeIdentityEncoder,
} from "../../api/productionService";

const describeChecks = (status) => {
  const smoke =
    status.smoke_check === "embed"
      ? `identity score after LoRA training, green from ${status.smoke_threshold}`
      : status.smoke_check === "vlm"
        ? "identity from the vision model after LoRA training"
        : "no identity score after LoRA training";
  const clips =
    status.clip_check === "embed"
      ? `clips held for review when the middle frame matches the keyframe under ${status.clip_floor}`
      : "clips compared with their keyframe by colour only";
  return `${smoke}; ${clips}.`;
};

const IdentityCheckControl = ({ showMessage }) => {
  const [status, setStatus] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getIdentityCheck());
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
    timer.current = setTimeout(load, 1500);
    return () => clearTimeout(timer.current);
  }, [running, status, load]);

  const act = async (call, verb) => {
    setBusy(true);
    try {
      const next = await call();
      setStatus(next);
      if (next?.error) showMessage?.(next.error, "warning");
    } catch (e) {
      showMessage?.(`Could not ${verb}: ${e.message || e}`, "error");
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  const job = status.install || {};
  const size = `${status.size_mb} MB`;
  const what = `${status.repo} (${status.license}, ${size}) from Hugging Face, pinned to commit ${String(status.revision).slice(0, 12)}, into ${status.location}`;

  return (
    <>
      <Line>
        {status.installed ? (
          <StatusPill label="Installed" tone="ok" tooltip={what} />
        ) : running ? (
          <StatusPill
            label={job.downloaded_mb > 0 ? `Installing ${job.progress}%` : "Installing"}
            tone="info"
            tooltip={what}
          />
        ) : (
          <StatusPill label="Not installed" tone="neutral" tooltip={what} />
        )}
        {!status.installed && !running && (
          <ActionButton
            onClick={() => act(installIdentityEncoder, "start the install")}
            loading={busy}
            tooltip={`Downloads ${what}. Nothing is downloaded until you click.`}
          >
            {`Install ${status.name} (${size})`}
          </ActionButton>
        )}
        {status.installed && (
          <ActionButton
            onClick={() => setConfirmRemove(true)}
            disabled={busy}
            tooltip={`Deletes ${status.location}; Install brings it back.`}
          >
            Remove
          </ActionButton>
        )}
      </Line>
      {!status.installed && (
        <Hint>Install downloads {what}. Nothing is downloaded until you click it.</Hint>
      )}
      <Hint>Now: {describeChecks(status)}</Hint>
      {job.state === "failed" && job.error && <Hint>Install failed: {job.error}</Hint>}
      <ConfirmActionDialog
        open={confirmRemove}
        reversible
        title={`Remove ${status.name}?`}
        description="The identity check goes back to what it did before the encoder: no identity score after LoRA training, and clips compared with their keyframe by colour. Scores already stored stay as they are."
        facts={[
          { label: "Deletes", value: status.location },
          { label: "Size", value: size },
        ]}
        confirmLabel="Remove"
        busy={busy}
        onConfirm={async () => {
          await act(removeIdentityEncoder, "remove it");
          setConfirmRemove(false);
        }}
        onClose={() => setConfirmRemove(false)}
      />
    </>
  );
};

export default IdentityCheckControl;
