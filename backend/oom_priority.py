"""Make the kernel's OOM killer sacrifice US, never the desktop session.

2026-08-04 client box incident: memory exhaustion during generation locked up the
whole Wayland desktop — the kernel's OOM killer has no reason to prefer a
50GB generator process over gnome-shell unless we tell it to. Raising our own
oom_score_adj (unprivileged processes may always RAISE it; children inherit)
turns "desktop dies" into "generation job dies with a clean error", which is
the correct failure mode for software running on end users' machines.

+500, not +1000: a genuinely worse offender (another runaway process) should
still lose first. Override via GUAARDVARK_OOM_SCORE_ADJ; set to 0 to disable.

NOT systemd-oomd: the stack is nohup'd from start.sh into the user session
scope — cgroup-level pressure kills would take out the whole session, the
exact opposite of the goal.
"""
import logging
import os

logger = logging.getLogger(__name__)


def apply_oom_score_adj(value: int | None = None) -> bool:
    """Write /proc/self/oom_score_adj. Returns True when applied."""
    if value is None:
        try:
            value = int(os.environ.get("GUAARDVARK_OOM_SCORE_ADJ", "500"))
        except ValueError:
            value = 500
    if value == 0:
        logger.info("oom_score_adj disabled (GUAARDVARK_OOM_SCORE_ADJ=0)")
        return False
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write(str(value))
        logger.info(
            "oom_score_adj set to %+d — under memory pressure the kernel kills "
            "this process, not the desktop", value,
        )
        return True
    except OSError as e:
        # Non-Linux, containers without /proc, or an attempt to LOWER below the
        # inherited value (needs CAP_SYS_RESOURCE). Non-fatal either way.
        logger.warning("could not set oom_score_adj (non-fatal): %s", e)
        return False
