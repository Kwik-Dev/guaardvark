import { useState, useEffect, useRef, useCallback } from "react";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

/**
 * Batch video queue state, WebSocket progress, and batch lifecycle handlers.
 */
export function useBatchVideo({ setError, setSuccess, computedParams } = {}) {
  const [activeBatchId, setActiveBatchId] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const [queue, setQueue] = useState([]);

  const pollingRef = useRef(null);
  const queuePollingRef = useRef(null);
  const socketRef = useRef(null);

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
          setQueue(data.data.queue || []);
        }
      }
    } catch {
      // ignore polling errors
    }
  }, []);

  const startPollingStatus = useCallback((batchId) => {
    stopPolling();
    if (socketRef.current?.connected) {
      socketRef.current.emit("subscribe", { job_id: batchId });
    }
    fetch(`${API_BASE}/batch-video/status/${batchId}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.success) {
          setBatchStatus(data.data);
          if (["completed", "error", "cancelled"].includes(data.data.status)) {
            fetchBatches();
          }
        }
      })
      .catch((e) => console.error(e));
  }, [stopPolling, fetchBatches]);

  useEffect(() => {
    socketRef.current = io(SOCKET_URL);
    socketRef.current.on("video_batch:update", (data) => {
      setBatchStatus(data);
      if (["completed", "error", "cancelled"].includes(data.status)) {
        fetchBatches();
      }
    });
    return () => {
      socketRef.current?.disconnect();
    };
  }, [fetchBatches]);

  useEffect(() => {
    fetchBatches();
    fetchQueue();
    queuePollingRef.current = setInterval(fetchQueue, 2000);
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
  }, [activeBatchId, fetchBatches, setError, setSuccess, stopPolling]);

  const handleCancelBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/batch/${batchId}/cancel`, { method: "POST" });
      if (res.ok) {
        await fetchBatches();
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
      }
    } catch {
      // ignore
    }
  }, [activeBatchId, fetchBatches, startPollingStatus]);

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
  };
}

export default useBatchVideo;