// frontend/src/components/settings/JobOrderSetting.jsx
import React, { useEffect, useState } from "react";
import { getJobPriority, setJobPriority } from "../../api/settingsService";
import { Cluster, Line, SettingChip } from "./ui";

const readFlag = (result) => {
  const on = result?.data?.job_priority_enabled ?? result?.job_priority_enabled;
  return typeof on === "boolean" ? on : null;
};

/**
 * Settings → General: the job order switch (backend/services/job_arbiter.py).
 * Off keeps today's order: each queue runs its own jobs first come, first
 * served. On ranks waiting work by class and lets the Activity page move it.
 */
const JobOrderSetting = ({ showMessage }) => {
  const [on, setOn] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    getJobPriority().then((result) => {
      if (!alive) return;
      const flag = readFlag(result);
      if (flag !== null) setOn(flag);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const toggle = async (next) => {
    const previous = on;
    setOn(next);
    setSaving(true);
    const result = await setJobPriority(next);
    setSaving(false);
    const stored = readFlag(result);
    if (result?.error || stored === null) {
      setOn(previous);
      showMessage?.("Job order was not saved", "error");
      return;
    }
    setOn(stored);
    showMessage?.(stored ? "Job order on" : "Job order off: each queue keeps its own order", "success");
  };

  return (
    <Cluster
      label="Job order"
      help="Off: each queue runs its jobs first come, first served, as it always has. On: jobs waiting for the graphics card go in class order: chat and voice first, then the renders and batches you started, then background work (indexing, autoresearch, self-improvement, scheduled tasks). Background work also pauses while you chat and moves up the longer it waits; the nightly autoresearch run waits for an idle machine; a running job is never stopped; a render that would unload a swarm agent's model waits for the agent unless you press Run now. The Activity page shows the line and lets you move items up or down. Other parts of the app pick the change up within 30 seconds."
    >
      <Line>
        <SettingChip
          label="Prioritise interactive work"
          on={on}
          onToggle={toggle}
          disabled={!loaded || saving}
          tooltip="Rank waiting jobs by class: interactive, then yours, then background"
          testId="job-order-setting"
        />
      </Line>
    </Cluster>
  );
};

export default JobOrderSetting;
