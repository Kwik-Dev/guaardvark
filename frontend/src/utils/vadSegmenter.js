/**
 * Voice activity segmentation over a stream of level readings.
 *
 * Fed one RMS level per audio frame with its timestamp, it reports where an
 * utterance starts and ends. The threshold is fixed, so how loud a person has
 * to speak does not move while they talk; the Voice page meter is where it is
 * set. Speech has to stay above `threshold` for `startConfirmMs` to
 * start (a key click does not), stays going while the level is above
 * `threshold * hysteresis`, and ends after `silenceMs` below that. Utterances
 * shorter than `minSpeechMs` are reported as discards; ones that reach
 * `maxSegmentMs` are cut there.
 *
 * @param {{threshold:number, hysteresis:number, startConfirmMs:number,
 *          silenceMs:number, minSpeechMs:number, maxSegmentMs:number}} initialConfig
 * @returns {{push:(level:number, now:number)=>(null|{type:string, speechMs?:number, reason?:string}),
 *            reset:()=>void, setConfig:(config:object)=>void, readonly speaking:boolean}}
 */
export function createVadSegmenter(initialConfig) {
  let config = { ...initialConfig };
  let speaking = false;
  let candidateSince = null;
  let speechStart = null;
  let lastLoud = null;

  const reset = () => {
    speaking = false;
    candidateSince = null;
    speechStart = null;
    lastLoud = null;
  };

  const push = (level, now) => {
    const value = Number.isFinite(level) ? level : 0;
    if (!speaking) {
      if (value >= config.threshold) {
        if (candidateSince === null) candidateSince = now;
        if (now - candidateSince >= config.startConfirmMs) {
          speaking = true;
          speechStart = candidateSince;
          lastLoud = now;
          candidateSince = null;
          return { type: "speech-start", at: speechStart };
        }
      } else {
        candidateSince = null;
      }
      return null;
    }

    if (value >= config.threshold * config.hysteresis) lastLoud = now;
    const speechMs = lastLoud - speechStart;

    if (now - speechStart >= config.maxSegmentMs) {
      reset();
      return { type: "speech-end", reason: "max", speechMs };
    }
    if (now - lastLoud >= config.silenceMs) {
      reset();
      return speechMs >= config.minSpeechMs
        ? { type: "speech-end", reason: "silence", speechMs }
        : { type: "discard", speechMs };
    }
    return null;
  };

  return {
    push,
    reset,
    setConfig(next) {
      config = { ...config, ...next };
    },
    get speaking() {
      return speaking;
    },
    get pending() {
      return candidateSince !== null;
    },
  };
}

export default createVadSegmenter;
