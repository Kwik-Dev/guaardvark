// Which installed LoRAs the Video Generator offers for the selected model.

/**
 * LoRAs that name `model` in applies_to and are not owned by a speed profile.
 * An empty applies_to matches no model, and `adapter: false` is never offered.
 * A speed profile's LoRAs are trained for that profile's steps, cfg and shift
 * (and Wan's pair is split per expert), so only the profile picker uses them.
 * @param {Array<object>} adapterModels  rows from GET /batch-video/models with type "lora"
 * @param {string} model                 selected generation model id
 * @param {object} [speedProfiles]       capabilities.speed_profiles of that model
 * @returns {Array<object>}
 */
export function applicableAdapters(adapterModels, model, speedProfiles) {
  const owned = new Set();
  Object.values(speedProfiles || {}).forEach((spec) => {
    if (spec?.lora) owned.add(spec.lora);
    Object.values(spec?.loras || {}).forEach((id) => owned.add(id));
  });
  return (adapterModels || []).filter(
    (m) => m.adapter !== false && (m.applies_to || []).includes(model) && !owned.has(m.id),
  );
}
