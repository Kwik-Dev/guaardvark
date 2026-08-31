// Accepts files dropped from the OS file browser and uploads them as Documents.
// Returns a stable `onDrop` handler + a `uploading` flag. The dropped files
// land under data/uploads/Videos/ (folder name is configurable) via the
// existing /api/files/upload pipeline.

import { useCallback, useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Route each dropped file to the right library folder (and tag its kind) by
// extension, so the Bin can accept video, audio, and image in one drop.
const EXT_KIND = {
  video: ["mp4", "mov", "webm", "mkv", "avi", "m4v"],
  audio: ["mp3", "wav", "flac", "m4a", "aac", "ogg"],
  image: ["jpg", "jpeg", "png", "webp", "gif", "bmp"],
};
const FOLDER_FOR_KIND = { video: "Videos", audio: "Audio", image: "Images" };

export function kindForFile(name = "", fallback = "video") {
  const ext = (name.split(".").pop() || "").toLowerCase();
  for (const [k, exts] of Object.entries(EXT_KIND)) if (exts.includes(ext)) return k;
  return fallback;
}

export function useExternalDrop({ folderName = "Videos", onUploaded }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);

  const handleDrop = useCallback(async (event) => {
    event.preventDefault();
    event.stopPropagation();

    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length === 0) return;

    setUploading(true);
    setError(null);
    const uploaded = [];

    try {
      for (const [idx, file] of files.entries()) {
        const kind = kindForFile(file.name);
        const form = new FormData();
        form.append("file", file);
        form.append("folder_name", FOLDER_FOR_KIND[kind] || folderName);
        const res = await axios.post(`${API_BASE}/files/upload`, form, {
          headers: { "Content-Type": "multipart/form-data" },
          onUploadProgress: (e) => {
            if (e.total) {
              const filePct = (e.loaded / e.total) * 100;
              setProgress(((idx + filePct / 100) / files.length) * 100);
            }
          },
        });
        const doc = res.data?.data || res.data?.document || res.data;
        if (doc?.id) uploaded.push({ ...doc, kind });
      }
      if (onUploaded) onUploaded(uploaded);
    } catch (e) {
      console.error("useExternalDrop: upload failed:", e);
      setError(e.response?.data?.error?.message || e.message || "Upload failed");
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }, [folderName, onUploaded]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  return { onDrop: handleDrop, onDragOver: handleDragOver, uploading, progress, error };
}
