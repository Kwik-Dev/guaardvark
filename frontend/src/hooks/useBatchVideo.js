import { useState, useEffect, useRef, useCallback } from "react";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const ACTIVE_STATUSES = new Set(["queued", "pending", "running"]);
const TERMINAL_STATUSES = new Set(["completed", "error", "cancelled"]);
const STATUS_POLL_MS = 2000;
const CLEARED_QUEUE_KEY = "clearedVideoQueueIds";

function readClearedQueueIds() {
  try {
    const stored = JSON.parse(localStorage.getItem(CLEARED_QUEUE_KEY) || "[]");
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

/**
 * Batch video queue state, WebSocket progress, and batch lifecycle handlers.
 * HTTP status polling runs while a batch is active so progress survives WS drops.
 */
export function useBatchVideo({ setError, setSuccess, computedParams } = {}) {
  const [activeBatchId, setActiveBatchId] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const [queue, setQueue] = useState([]);

  const pollingRef = useRef(null);
  const queuePollingRef = useRef(null);
  const socketRef = useRef(null);
  const activeBatchIdRef = useRef(null);

  useEffect(() => {
    activeBatchIdRef.current = activeBatchId;
  }, [activeBatchId]);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const fetchBatches = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const sorted = (data.data.batches || []).sort((a, b) => {
            const ta = a.start_time || a.end_time || "";
            const tb = b.start_time || b.end_time || "";
            return tb.localeCompare(ta);
          });
          setBatches(sorted);
        }
      }
    } catch {
      // ignore
    }
  }, []);

  const fetchQueue = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/queue`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const cleared = new Set(readClearedQueueIds());
          const visible = (data.data.queue || []).filter(
            (q) => !(TERMINAL_STATUSES.has(q.status) && cleared.has(q.batch_id)),
          );
          setQueue(visible);
        }
      }
    } catch {
      // ignore polling errors
    }
  }, []);

  const handleClearCompletedQueue = useCallback(() => {
    const doneIds = queue
      .filter((q) => TERMINAL_STATUSES.has(q.status))
      .map((q) => q.batch_id);
    if (doneIds.length === 0) return;
    try {
      const stored = readClearedQueueIds();
      localStorage.setItem(
        CLEARED_QUEUE_KEY,
        JSON.stringify(Array.from(new Set([...stored, ...doneIds]))),
      );
    } catch (e) {
      console.error("Failed to save cleared video queue items:", e);
    }
    setQueue((prev) => prev.filter((q) => !TERMINAL_STATUSES.has(q.status)));
  }, [queue]);

  const fetchStatusOnce = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/status/${batchId}`);
      const data = await res.json();
      if (data.success) {
        setBatchStatus(data.data);
        if (TERMINAL_STATUSES.has(data.data.status)) {
          stopPolling();
          fetchBatches();
        }
        return data.data;
      }
    } catch (e) {
      console.error(e);
    }
    return null;
  }, [stopPolling, fetchBatches]);

  const startPollingStatus = useCallback((batchId) => {
    stopPolling();
    if (socketRef.current?.connected) {
      socketRef.current.emit("subscribe", { job_id: batchId });
    }
    // Immediate snapshot, then keep polling while active (WS is the fast path).
    fetchStatusOnce(batchId).then((snap) => {
      if (snap && ACTIVE_STATUSES.has(snap.status)) {
        pollingRef.current = setInterval(() => {
          fetchStatusOnce(batchId);
        }, STATUS_POLL_MS);
      }
    });
  }, [stopPolling, fetchStatusOnce]);

  useEffect(() => {
    socketRef.current = io(SOCKET_URL);
    socketRef.current.on("video_batch:update", (data) => {
      // Only apply WS updates for the active batch (or unset until first poll).
      if (
        activeBatchIdRef.current &&
        data?.batch_id &&
        data.batch_id !== activeBatchIdRef.current
      ) {
        return;
      }
      setBatchStatus(data);
      if (TERMINAL_STATUSES.has(data.status)) {
        stopPolling();
        fetchBatches();
      }
    });
    return () => {
      socketRef.current?.disconnect();
    };
  }, [fetchBatches, stopPolling]);

  useEffect(() => {
    fetchBatches();
    fetchQueue();
    queuePollingRef.current = setInterval(fetchQueue, STATUS_POLL_MS);
    return () => {
      stopPolling();
      if (queuePollingRef.current) clearInterval(queuePollingRef.current);
    };
  }, [fetchBatches, fetchQueue, stopPolling]);

  const handleDownloadBatch = useCallback(async (batchId) => {
    window.open(`${API_BASE}/batch-video/download/${batchId}`, "_blank");
  }, []);

  const handleCombineFrames = useCallback(async (batchId, itemId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/combine-frames/${batchId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fps: computedParams?.fps || 7, item_id: itemId }),
      });
      if (res.ok) {
        await fetchBatches();
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
      }
    } catch {
      // ignore
    }
  }, [activeBatchId, computedParams, fetchBatches, startPollingStatus]);

  const handleDeleteBatch = useCallback(async (batchId, displayName) => {
    if (!window.confirm(
      `Delete "${displayName || batchId.slice(0, 8)}" and all of its videos? This can't be undone.`,
    )) {
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/batch-video/delete/${batchId}`, { method: "DELETE" });
      if (res.ok) {
        await fetchBatches();
        await fetchQueue();
        if (activeBatchId === batchId) {
          setActiveBatchId(null);
          setBatchStatus(null);
          stopPolling();
        }
        setSuccess?.("Batch deleted.");
      } else {
        const data = await res.json().catch(() => ({}));
        setError?.(data.error || `Delete failed: HTTP ${res.status}`);
      }
    } catch (e) {
      setError?.(`Delete failed: ${e.message}`);
    }
  }, [activeBatchId, fetchBatches, fetchQueue, setError, setSuccess, stopPolling]);

  const handleCancelBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/batch/${batchId}/cancel`, { method: "POST" });
      if (res.ok) {
        await fetchBatches();
        await fetchQueue();
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
      }
    } catch {
      // ignore
    }
  }, [activeBatchId, fetchBatches, fetchQueue, startPollingStatus]);

  const handleRetryBatch = useCallback(async (batchId) => {
    try {
      setError?.("");
      const res = await fetch(`${API_BASE}/batch-video/retry/${batchId}`, { method: "POST" });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setError?.(errData.error || `Retry failed: HTTP ${res.status}`);
        return;
      }
      const data = await res.json();
      if (data.success && data.data?.batch_id) {
        const newBatchId = data.data.batch_id;
        setActiveBatchId(newBatchId);
        setBatchStatus(null);
        startPollingStatus(newBatchId);
        await fetchBatches();
        await fetchQueue();
        setSuccess?.(`Retried as new batch ${newBatchId}. Original failed batch is preserved in history.`);
      }
    } catch (e) {
      setError?.(`Retry failed: ${e.message}`);
    }
  }, [fetchBatches, fetchQueue, setError, setSuccess, startPollingStatus]);

  return {
    activeBatchId,
    setActiveBatchId,
    batchStatus,
    setBatchStatus,
    batches,
    queue,
    fetchBatches,
    fetchQueue,
    startPollingStatus,
    stopPolling,
    handleDownloadBatch,
    handleCombineFrames,
    handleDeleteBatch,
    handleCancelBatch,
    handleRetryBatch,
    handleClearCompletedQueue,
  };
}

export default useBatchVideo;
