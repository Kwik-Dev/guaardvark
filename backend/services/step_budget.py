#!/usr/bin/env python3
"""Cross-tier step budget and tier telemetry — pure accounting, no memory stubs."""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TierTelemetry:
    """Captured per interaction for analytics and future auto-reflex promotion."""
    tier: int
    latency_ms: int
    tools_called: List[str] = field(default_factory=list)
    tool_params: List[Dict] = field(default_factory=list)
    escalated_from: Optional[int] = None
    escalation_reason: Optional[str] = None
    message_hash: str = ""
    success: bool = True
    model: str = ""
    timestamp: str = ""
    total_agent_steps: int = 0
    budget_remaining: int = 0
    budget_total: int = 20
    budget_charges: int = 0
    intent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "tier": self.tier,
            "latency_ms": self.latency_ms,
            "tools_called": self.tools_called,
            "tool_params": self.tool_params,
            "escalated_from": self.escalated_from,
            "escalation_reason": self.escalation_reason,
            "message_hash": self.message_hash,
            "success": self.success,
            "model": self.model,
            "timestamp": self.timestamp,
            "total_agent_steps": self.total_agent_steps,
            "budget_remaining": self.budget_remaining,
            "budget_total": self.budget_total,
            "budget_charges": self.budget_charges,
        }
        if self.intent:
            d["intent"] = self.intent
        return d

    @staticmethod
    def hash_message(message: str) -> str:
        """One-way hash so telemetry never stores raw user messages."""
        normalized = message.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass
class StepBudget:
    """
    First-class cross-tier termination budget (pure accounting).

    Memory and facts live in memory_api / FactsRegistry — not here.
    """
    total: int = 20
    used: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def is_exhausted(self) -> bool:
        return self.remaining <= 0

    def charge(self, amount: int, tier: int, reason: str = "") -> bool:
        """Charge steps against the budget. Returns False if budget exhausted."""
        if amount <= 0:
            return True
        self.used += amount
        self.history.append({
            "tier": tier,
            "amount": amount,
            "reason": reason or "unspecified",
            "remaining_after": self.remaining,
        })
        return self.remaining > 0

    def consume(self, amount: int, tier: int, reason: str = "") -> bool:
        """Alias for charge()."""
        return self.charge(amount, tier, reason)

    def on_escalation(self, from_tier: int, cost: int = 2, reason: str = "tier escalation"):
        """Deduct a cost when moving from one tier to a heavier one."""
        self.charge(cost, from_tier, reason)

    def to_context(self) -> str:
        if self.remaining <= 3:
            urgency = " (BUDGET IS LOW — be extremely efficient and prefer short paths)"
        elif self.remaining <= 8:
            urgency = " (budget is getting tight)"
        else:
            urgency = ""
        return (
            f"Cross-tier agentic step budget: used {self.used}/{self.total}, "
            f"{self.remaining} remaining{urgency}. "
            "Do not waste steps on unnecessary exploration."
        )

    def to_llm_summary(self) -> str:
        pct = int((self.used / self.total) * 100) if self.total > 0 else 0
        return f"[BUDGET: {self.remaining}/{self.total} steps left ({pct}% used)]"

    def to_telemetry(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "used": self.used,
            "remaining": self.remaining,
            "history": self.history[-10:],
        }

    @classmethod
    def from_total(cls, total: int) -> "StepBudget":
        return cls(total=total)

    @classmethod
    def from_hw_policy(cls, hardware: dict) -> "StepBudget":
        try:
            from .hardware_policy import model_tier
            gpu = hardware.get("gpu", {}) or {}
            ram = hardware.get("ram", {}) or {}
            arch = hardware.get("arch", "")
            tier = model_tier(ram.get("total_gb", 16), gpu, arch)
            vram = gpu.get("vram_mb", 16000)
            base = 20
            if vram < 16000:
                base = 10
            elif vram > 24000:
                base = 30
            if "1b" in tier.get("chat", ""):
                base = max(5, base // 2)
            return cls(total=base)
        except Exception:
            return cls(total=20)
