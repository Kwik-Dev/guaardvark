/** Human-readable VideoGen batch stage labels. */
export const VIDEO_GEN_STAGE_LABELS = {
  queued: "Queued",
  gpu_wait: "Waiting for GPU",
  storyboard: "Storyboard",
  director: "Director",
  keyframe: "Keyframe",
  generate: "Generating",
  post: "Post-process",
  register: "Registering",
  done: "Done",
};

export function videoGenStageLabel(stage) {
  if (!stage) return "";
  return VIDEO_GEN_STAGE_LABELS[stage] || stage;
}
