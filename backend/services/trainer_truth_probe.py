"""TrainerTruthProbe — ground-truth labels from the vision trainer's DOM.

The learning pipeline's original sin (2026-08-01 audit): the `success` recorded
per servo click was "did xdotool return 0", and the training LABEL
(`target_actual`) was the model's own prediction. The system trained on a
mirror. This probe replaces the mirror with a window: during training sessions
on the vision trainer, it reads the page's OWN scoreboard and target position
over BiDi, so every recorded click carries what actually happened.

Contract (all methods are best-effort and never raise into the servo path):
  probe = TrainerTruthProbe()          # connects lazily; inert if BiDi/page absent
  snap = probe.before()                # counters + target center for THIS frame
  truth = probe.after(snap)            # tri-state outcome dict, or None

Outcome semantics — tri-state, never collapsed:
  true_hit=True   the trainer's click counter advanced (dot was hit)
  true_hit=False  the miss counter advanced (arena background was clicked)
  true_hit=None   neither moved (header/off-arena click) — UNSCORED, dataset
                  builders must DROP these rows, not count them as misses.

One long-lived BiDi session per probe instance: Firefox caps concurrent
sessions and fails session.new silently once exhausted — connect-per-probe at
~2 probes/click would drain the cap in minutes (documented in
reddit_outreach.py). The session is created on first use and reused; any error
tears it down and the next call reconnects.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BIDI_URL = "ws://localhost:9222/session"
TRAINER_MARKER = "vision_trainer"  # substring of location.href that activates truth

# Reads counters + current target center in SCREEN coordinates (mozInnerScreenX/Y
# converts the page-relative rect; same proven pattern as servo_calibrate.py).
_SNAPSHOT_JS = """(() => {
  if (location.href.indexOf('vision_trainer') === -1) return "not_trainer";
  const c = document.getElementById('clicks');
  const m = document.getElementById('misses');
  if (!c || !m) return "no_counters";
  const t = document.querySelector('.target');
  const out = {clicks: parseInt(c.textContent)||0, misses: parseInt(m.textContent)||0};
  if (t) {
    const r = t.getBoundingClientRect();
    out.target_cx = Math.round(r.left + r.width/2 + window.mozInnerScreenX);
    out.target_cy = Math.round(r.top + r.height/2 + window.mozInnerScreenY);
    out.label = (t.textContent || '').trim();
  }
  return JSON.stringify(out);
})()"""


class TrainerTruthProbe:
    """Reads hit/miss ground truth from the trainer page. Inert off-trainer."""

    def __init__(self, poll_timeout_s: float = 1.0, poll_interval_s: float = 0.05):
        self._ws = None
        self._ctx: Optional[str] = None
        self._msg_id = 0
        self._poll_timeout_s = poll_timeout_s
        self._poll_interval_s = poll_interval_s
        # After the first hard failure we stop trying for this instance — a
        # probe on a box with no BiDi must cost ~nothing per click.
        self._dead = False

    # ── BiDi plumbing (one session, reused) ──────────────────────────────

    def _connect(self) -> bool:
        if self._dead:
            return False
        if self._ws is not None:
            return True
        try:
            import websocket as _ws
            self._ws = _ws.create_connection(BIDI_URL, timeout=3, suppress_origin=True)
            self._send({"method": "session.new", "params": {"capabilities": {}}})
            tree = self._send({"method": "browsingContext.getTree", "params": {}})
            self._ctx = tree["result"]["contexts"][0]["context"]
            return True
        except Exception as e:
            logger.debug(f"truth probe: BiDi unavailable ({e}) — probe inert")
            self._teardown(dead=True)
            return False

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._msg_id += 1
        payload = {"id": self._msg_id, **payload}
        self._ws.send(json.dumps(payload))
        while True:
            resp = json.loads(self._ws.recv())
            if resp.get("id") == self._msg_id:
                return resp

    def _teardown(self, dead: bool = False) -> None:
        try:
            if self._ws is not None:
                try:
                    self._send({"method": "session.end", "params": {}})
                except Exception:
                    pass
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        self._ctx = None
        if dead:
            self._dead = True

    def close(self) -> None:
        """End the BiDi session cleanly (call at session teardown)."""
        self._teardown(dead=False)

    def _snapshot(self) -> Optional[Dict[str, Any]]:
        """One counters+target read. None = not on trainer / not readable."""
        if not self._connect():
            return None
        try:
            r = self._send({"method": "script.evaluate", "params": {
                "expression": _SNAPSHOT_JS,
                "target": {"context": self._ctx},
                "awaitPromise": False,
            }})
            val = r.get("result", {}).get("result", {}).get("value")
            if not val or val in ("not_trainer", "no_counters"):
                return None
            return json.loads(val)
        except Exception as e:
            # Connection went stale (page reload, browser restart) — drop the
            # session; next call reconnects fresh rather than staying dead.
            logger.debug(f"truth probe: snapshot failed ({e}) — will reconnect")
            self._teardown(dead=False)
            return None

    # ── The two probe points ─────────────────────────────────────────────

    def before(self) -> Optional[Dict[str, Any]]:
        """Call adjacent to the servo's screen capture.

        The returned snapshot describes THE FRAME THE MODEL SEES — on a hit
        the trainer respawns the dot synchronously, so target position must
        come from before the click, never after.
        """
        return self._snapshot()

    def after(self, before_snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Poll counters until one moves (or timeout). Returns the truth dict.

        xdotool returns when the XTEST event is queued; Firefox dispatches the
        DOM click tens of ms later — hence the short poll instead of a single
        read.
        """
        if before_snap is None:
            return None
        deadline = time.monotonic() + self._poll_timeout_s
        clicks0 = before_snap.get("clicks", 0)
        misses0 = before_snap.get("misses", 0)
        true_hit: Optional[bool] = None
        while time.monotonic() < deadline:
            snap = self._snapshot()
            if snap is not None:
                if snap.get("clicks", 0) > clicks0:
                    true_hit = True
                    break
                if snap.get("misses", 0) > misses0:
                    true_hit = False
                    break
            time.sleep(self._poll_interval_s)

        return {
            "true_hit": true_hit,  # None = UNSCORED (header/off-arena), drop in builders
            "target_cx": before_snap.get("target_cx"),
            "target_cy": before_snap.get("target_cy"),
            "label": before_snap.get("label", ""),
            "truth_source": "vision_trainer_dom",
        }
