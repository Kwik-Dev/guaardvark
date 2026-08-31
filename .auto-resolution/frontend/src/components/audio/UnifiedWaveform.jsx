// frontend/src/components/audio/UnifiedWaveform.jsx
import React, { useEffect, useRef, useState } from "react";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";

/**
 * UnifiedWaveform - The single source of truth for audio visualization in Guaardvark.
 * Supports:
 * - 'live': Real-time input from audioLevels array
 * - 'playback': Visualizes a static audio file from a URL
 * - 'subtle': Background breathing effect
 */
const UnifiedWaveform = ({
  mode = "live",
  audioLevels = [],
  src = null,
  isActive = false,
  color,
  height = 60,
  numBars = 60,
  barGap = 3,
  borderRadius = 2,
}) => {
  const theme = useTheme();
  const canvasRef = useRef(null);
  const animationFrameRef = useRef(null);
  const smoothedLevelsRef = useRef(new Float32Array(numBars));
  const mainColor = color || theme.palette.primary.main;

  // Internal state for playback mode
  const [playbackLevels, setPlaybackLevels] = useState([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let idleFrames = 0;

    const draw = () => {
      const rect = canvas.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const w = rect.width;
      const h = height;

      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
        ctx.scale(dpr, dpr);
      }

      ctx.clearRect(0, 0, w, h);

      // Determine which levels to use
      let currentLevels = audioLevels;
      if (mode === "playback" && !isActive) {
        // Static "pre-generated" look if not playing
        currentLevels = playbackLevels.length ? playbackLevels : new Array(numBars).fill(0.1);
      }

      const barWidth = (w - (numBars - 1) * barGap) / numBars;
      const centerY = h / 2;

      for (let i = 0; i < numBars; i++) {
        const rawLevel = currentLevels[i] || 0;
        
        // Rise/Fall smoothing
        const smoothing = rawLevel > smoothedLevelsRef.current[i] ? 0.3 : 0.15;
        smoothedLevelsRef.current[i] += (rawLevel - smoothedLevelsRef.current[i]) * smoothing;
        
        const level = smoothedLevelsRef.current[i];
        const barH = Math.max(2, level * h * 0.9);
        const x = i * (barWidth + barGap);

        ctx.fillStyle = mainColor;
        ctx.globalAlpha = isActive ? 0.8 : 0.3;
        
        // Draw centered bar
        ctx.beginPath();
        if (ctx.roundRect) {
          ctx.roundRect(x, centerY - barH / 2, barWidth, barH, borderRadius);
        } else {
          ctx.rect(x, centerY - barH / 2, barWidth, barH);
        }
        ctx.fill();
      }

      // Stop the rAF chain when there's nothing to animate. We give the
      // smoothing 60 frames to settle into the static playback look first,
      // otherwise pausing freezes the bars mid-transition and looks janky.
      if (!isActive && mode !== "subtle") {
        idleFrames += 1;
        if (idleFrames > 60) {
          animationFrameRef.current = null;
          return;
        }
      } else {
        idleFrames = 0;
      }

      animationFrameRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animationFrameRef.current);
  }, [mode, audioLevels, playbackLevels, isActive, mainColor, height, numBars, barGap, borderRadius]);

  // Decode the REAL audio file into an amplitude envelope via the WebAudio API.
  // (Previously this drew a fake sin()+random() "aesthetic" waveform that showed
  // bars unrelated to the actual audio — a placebo. Now we read the file.)
  useEffect(() => {
    if (mode !== "playback" || !src) return;

    let cancelled = false;
    let audioCtx;

    const decode = async () => {
      try {
        const resp = await fetch(src);
        if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`);
        const arrayBuf = await resp.arrayBuffer();

        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) throw new Error("WebAudio API unavailable");
        audioCtx = new Ctx();
        // decodeAudioData is async/off the render thread — large files won't freeze UI.
        const audioBuf = await audioCtx.decodeAudioData(arrayBuf);
        if (cancelled) return;

        // Downsample channel 0 into numBars RMS buckets = perceived loudness per slice.
        const channel = audioBuf.getChannelData(0);
        const block = Math.floor(channel.length / numBars) || 1;
        const levels = new Array(numBars).fill(0);
        for (let i = 0; i < numBars; i++) {
          const start = i * block;
          let sum = 0;
          for (let j = 0; j < block; j++) {
            const s = channel[start + j] || 0;
            sum += s * s;
          }
          levels[i] = Math.sqrt(sum / block);
        }
        // Normalize to the tallest bucket so quiet files still render visibly.
        const peak = Math.max(...levels, 0.0001);
        const norm = levels.map((v) => Math.max(0.05, v / peak));
        if (!cancelled) setPlaybackLevels(norm);
      } catch (err) {
        // Honest failure: a flat low baseline, NOT invented bars that would imply
        // we analyzed audio we couldn't actually read.
        if (!cancelled) {
          console.warn("UnifiedWaveform: could not decode audio for", src, err);
          setPlaybackLevels(new Array(numBars).fill(0.08));
        }
      } finally {
        if (audioCtx && audioCtx.state !== "closed") {
          try { await audioCtx.close(); } catch (_) { /* ignore */ }
        }
      }
    };

    decode();
    return () => {
      cancelled = true;
      if (audioCtx && audioCtx.state !== "closed") {
        try { audioCtx.close(); } catch (_) { /* ignore */ }
      }
    };
  }, [mode, src, numBars]);

  return (
    <Box sx={{ width: "100%", height, position: "relative", overflow: "hidden" }}>
      <canvas ref={canvasRef} style={{ display: "block" }} />
    </Box>
  );
};

export default UnifiedWaveform;
