import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { getCeleryHealth, getBackendHealth, getDbHealth, getRedisHealth } from '../api';

const HealthContext = createContext();

export const useHealth = () => {
  const context = useContext(HealthContext);
  if (!context) {
    throw new Error('useHealth must be used within a HealthProvider');
  }
  return context;
};

export const HealthProvider = ({ children }) => {
  const [healthData, setHealthData] = useState({
    backend: null,
    db: null,
    celery: null,
    redis: null,
    lastUpdated: null,
    isLoading: false,
    errors: {}
  });

  const [subscribers, setSubscribers] = useState(new Set());

  const updateHealthData = useCallback(async (force = false) => {
    const now = Date.now();
    const cacheTimeout = 30000; // 30 seconds cache

    // Skip if not forced and cache is still valid
    if (!force && healthData.lastUpdated && (now - healthData.lastUpdated) < cacheTimeout) {
      return healthData;
    }

    setHealthData(prev => ({ ...prev, isLoading: true }));

    const results = await Promise.allSettled([
      getBackendHealth(),
      getDbHealth(),
      getCeleryHealth(),
      getRedisHealth()
    ]);

    const [backendRes, dbRes, celeryRes, redisRes] = results;

    const celeryValue = celeryRes.status === 'fulfilled' ? celeryRes.value : null;
    const isCeleryBusy = celeryValue && (celeryValue.status === 'busy' || (celeryValue.message || '').toLowerCase().includes('busy'));

    const newData = {
      backend: backendRes.status === 'fulfilled' ? backendRes.value : null,
      db: dbRes.status === 'fulfilled' ? dbRes.value : null,
      celery: celeryValue,
      redis: redisRes.status === 'fulfilled' ? redisRes.value : null,
      lastUpdated: now,
      isLoading: false,
      errors: {
        backend: backendRes.status === 'rejected' ? backendRes.reason?.message : null,
        db: dbRes.status === 'rejected' ? dbRes.reason?.message : null,
        // Do not surface celery "busy" (during long GPU tasks like LoRA training) as an error.
        // Prevents repeated 503/timeout console spam while the worker is legitimately occupied.
        // The CastMemberPage now tracks its own training jobs via unified progress.
        celery: (celeryRes.status === 'rejected' || (celeryValue && celeryValue.status === 'down' && !isCeleryBusy))
          ? (celeryRes.reason?.message || celeryValue?.error)
          : null,
        redis: redisRes.status === 'rejected' ? redisRes.reason?.message : null
      }
    };

    setHealthData(newData);
    return newData;
  }, [healthData.lastUpdated]);

  // Background poll so the app notices the backend going down/coming back without a
  // manual reload. updateHealthData's identity changes every poll (its deps include
  // lastUpdated), so we drive the interval off a ref to avoid resetting it each tick.
  const updateRef = useRef(updateHealthData);
  useEffect(() => {
    updateRef.current = updateHealthData;
  }, [updateHealthData]);
  useEffect(() => {
    updateRef.current(true); // prime immediately on mount
    const id = setInterval(() => updateRef.current(true), 10000);
    return () => clearInterval(id);
  }, []);

  // Subscribe/unsubscribe mechanism for components
  const subscribe = useCallback((callback) => {
    setSubscribers(prev => new Set([...prev, callback]));
    return () => {
      setSubscribers(prev => {
        const newSet = new Set(prev);
        newSet.delete(callback);
        return newSet;
      });
    };
  }, []);

  // Notify subscribers when health data changes
  useEffect(() => {
    subscribers.forEach(callback => callback(healthData));
  }, [healthData, subscribers]);

  // Confirmed offline = we have polled at least once (lastUpdated set) and the backend
  // health probe failed (null). Distinct from the initial pre-poll state where backend
  // is null simply because we haven't checked yet.
  const isBackendOffline = healthData.lastUpdated !== null && healthData.backend === null;

  const value = {
    healthData,
    updateHealthData,
    subscribe,
    isBackendOffline,
    // Convenience getters
    getCeleryHealth: () => healthData.celery,
    getBackendHealth: () => healthData.backend,
    getDbHealth: () => healthData.db,
    getRedisHealth: () => healthData.redis,
    getErrors: () => healthData.errors,
    isLoading: healthData.isLoading,
    lastUpdated: healthData.lastUpdated
  };

  return (
    <HealthContext.Provider value={value}>
      {children}
    </HealthContext.Provider>
  );
};
