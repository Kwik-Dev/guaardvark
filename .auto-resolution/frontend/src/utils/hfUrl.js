// Client-side Hugging Face paste checks. Copy matches parse_hf_url on the server
// so a bad host fails before the round-trip.

export function hfUrlClientError(raw) {
  const url = (raw || "").trim();
  if (!url) return "Paste a Hugging Face URL or org/repo.";
  const lower = url.toLowerCase();
  const hasScheme = lower.includes("://");
  const isHf = lower.includes("huggingface.co") || lower.includes("hf.co");
  if (hasScheme && !isHf) {
    return "Only Hugging Face URLs or org/repo ids are accepted.";
  }
  if (
    /huggingface\.co\/(datasets|spaces)\b/i.test(url) ||
    /hf\.co\/(datasets|spaces)\b/i.test(url) ||
    /^(datasets|spaces)\//i.test(url)
  ) {
    return "That is a Hugging Face dataset or Space, not a model repo. Paste a model URL (huggingface.co/org/repo).";
  }
  return "";
}

export function formatWeightSize(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "";
  return ` (${(n / 1024 ** 3).toFixed(2)} GB)`;
}

export function selectedBytes(files, selected) {
  return (files || [])
    .filter((f) => selected.includes(f.src))
    .reduce((sum, f) => sum + (Number(f.size) || 0), 0);
}
