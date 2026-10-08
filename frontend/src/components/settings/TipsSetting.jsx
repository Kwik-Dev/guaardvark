// frontend/src/components/settings/TipsSetting.jsx
import React from "react";
import { useAppStore } from "../../stores/useAppStore";
import { Cluster, Line, SettingChip } from "./ui";

/** Settings → General: the on/off switch for the "Did you know" tips. Stored per browser. */
const TipsSetting = () => {
  const tipsEnabled = useAppStore((s) => s.tipsEnabled);
  const setTipsEnabled = useAppStore((s) => s.setTipsEnabled);
  return (
    <Cluster
      label="Tips"
      help="A short “Did you know” card at the bottom left, at most one per visit, about something the interface can do. Saved in this browser."
    >
      <Line>
        <SettingChip label="Did you know tips" on={tipsEnabled} onToggle={setTipsEnabled} testId="tips-setting" />
      </Line>
    </Cluster>
  );
};

export default TipsSetting;
