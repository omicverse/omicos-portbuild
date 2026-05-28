"""SkillOpt Algorithm 1 — runnable state machine spec.

Direct transcription of arXiv:2605.23904v2 Algorithm 1 (Appendix C.1) into
Python dataclasses + a deterministic `OptimizerState` that the LLM-driven
optimizer.py will manipulate. This module is intentionally LLM-free so the
loop's structure can be unit-tested in isolation from any model call.

The paper's variable names are preserved (s_cur, s_best, C, B, m_meta) so the
implementation can be audited line-by-line against the paper.

Reference (Algo 1, line numbers from §C.1):
  1: s_cur ← s_0, s_best ← s_0, C ← ∅, B ← [], m_meta ← ∅
  2: score_cur ← Evaluate(M, h, s_0, D_sel); score_best ← score_cur
  3: C[Hash(s_0)] ← score_cur
  4: for e = 1 to E do
  5:     shuffle D_train into rollout batches; reset B ← []
  6:     for each optimization step in epoch e do
  7:         collect A rollout batches by executing h(M, x, s_cur)
  8:         split rollout evidence into failures and successes,
                     then into minibatches of size B_m
  9-11:      analyst_error → analyst_success → merge_failure → merge_success
                     → merge_final (failure-prioritized)
  12:        ranking: keep at most L_t edits
  13:        apply selected edits to obtain candidate skill s̃
  14-19:     if Hash(s̃) ∈ C: score_cand ← C[Hash(s̃)]
                     else evaluate on D_sel and cache
  20-24:     if score_cand > score_cur: accept; promote best if also > best
  25-27:     else: add rejected edits AND failure patterns to B
  28:    end for
  29-32: if e ≥ 2 and slow update enabled: longitudinal compare;
                     ask O for protected longitudinal guidance; validate via D_sel
  33-35: if e ≥ 2 and optimizer memory enabled: update m_meta
  36: end for
  37: score_test ← Evaluate(M, h, s_best, D_test)
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional


# --------------------------------------------------------------------------- #
# Hyperparameter contract (paper §4 + §C.3 defaults)                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OptimizerConfig:
    """Loop hyperparameters. Defaults are the paper's defaults (§4)."""

    epochs: int = 4
    rollout_batch_size_B: int = 40
    accumulation_factor_A: int = 1
    reflection_minibatch_size_Bm: int = 8
    edit_budget_Lt: int = 4
    lr_schedule: Literal["constant", "linear", "cosine", "autonomous"] = "constant"
    lr_floor: int = 2
    slow_update_enabled: bool = True
    slow_update_samples: int = 20
    meta_skill_enabled: bool = True
    edit_mode: Literal["patch", "rewrite_from_suggestions"] = "patch"
    teacher_refinement_rounds: int = 3
    analyst_workers_parallel: int = 16
    merge_batch_size: int = 8
    # Validation gate is STRICTLY greater than current selection score
    # (paper §3.5: "ties are rejected").
    validation_gate_strict: bool = True

    def lr_for_epoch(self, e: int) -> int:
        """Edit budget L_t at epoch e (1-indexed, like Algorithm 1)."""
        if self.lr_schedule == "constant":
            return self.edit_budget_Lt
        # cosine: from Lt down to lr_floor across E epochs
        if self.epochs <= 1:
            return self.edit_budget_Lt
        progress = (e - 1) / max(self.epochs - 1, 1)
        if self.lr_schedule == "linear":
            value = self.edit_budget_Lt - progress * (
                self.edit_budget_Lt - self.lr_floor
            )
            return max(int(round(value)), self.lr_floor)
        if self.lr_schedule == "cosine":
            import math

            value = self.lr_floor + 0.5 * (self.edit_budget_Lt - self.lr_floor) * (
                1 + math.cos(math.pi * progress)
            )
            return max(int(round(value)), self.lr_floor)
        # autonomous: caller decides; default to floor
        return self.lr_floor


# --------------------------------------------------------------------------- #
# Trajectory and rollout primitives                                           #
# --------------------------------------------------------------------------- #


@dataclass
class Trajectory:
    """One rollout's evidence. Harness-agnostic — adapter populates it."""

    task_id: str
    task: dict[str, Any]
    skill_hash: str
    score: float
    success: bool
    trace: str = ""
    final_answer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RejectedEdit:
    """One rejected step. Stored in B for later analyst calls to avoid."""

    epoch: int
    step: int
    edits_json: str  # the merged patch that was tried, serialized
    score_drop: float
    failure_summary: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StepBuffer:
    """Epoch-local buffer B from Algorithm 1.

    Carries (a) rejected edits and their score drops, (b) observed failure
    patterns from this epoch's analyst_error calls. Reset at every epoch
    boundary (Algorithm 1 line 5: "reset B ← []").
    """

    rejected: list[RejectedEdit] = field(default_factory=list)
    failure_patterns: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.rejected.clear()
        self.failure_patterns.clear()

    def add_rejected(self, edit: RejectedEdit) -> None:
        self.rejected.append(edit)

    def add_failure_patterns(self, patterns: list[dict[str, Any]]) -> None:
        self.failure_patterns.extend(patterns)

    def as_optimizer_context(self) -> dict[str, Any]:
        """Serializable view passed back to analyst_* prompts."""
        return {
            "rejected_edits": [
                {
                    "epoch": r.epoch,
                    "step": r.step,
                    "edits_json": r.edits_json,
                    "score_drop": r.score_drop,
                }
                for r in self.rejected
            ],
            "observed_failure_patterns": self.failure_patterns,
        }


# --------------------------------------------------------------------------- #
# Selection-score cache C                                                     #
# --------------------------------------------------------------------------- #


def skill_hash(skill_text: str) -> str:
    return hashlib.sha256(skill_text.encode("utf-8")).hexdigest()


@dataclass
class SelectionCache:
    """Hash → selection-split score. Algorithm 1 line 3."""

    _data: dict[str, float] = field(default_factory=dict)

    def get(self, h: str) -> Optional[float]:
        return self._data.get(h)

    def put(self, h: str, score: float) -> None:
        self._data[h] = score

    def __contains__(self, h: str) -> bool:
        return h in self._data

    def __len__(self) -> int:
        return len(self._data)


# --------------------------------------------------------------------------- #
# Optimizer state                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class OptimizerState:
    """The five state variables from Algorithm 1 line 1:
    s_cur, s_best, C, B, m_meta — plus their scores.
    """

    s_cur: str
    s_best: str
    score_cur: float
    score_best: float
    C: SelectionCache = field(default_factory=SelectionCache)
    B: StepBuffer = field(default_factory=StepBuffer)
    m_meta: str = ""

    @classmethod
    def initial(
        cls,
        s0: str,
        score_s0_on_sel: float,
    ) -> "OptimizerState":
        cache = SelectionCache()
        cache.put(skill_hash(s0), score_s0_on_sel)
        return cls(
            s_cur=s0,
            s_best=s0,
            score_cur=score_s0_on_sel,
            score_best=score_s0_on_sel,
            C=cache,
            B=StepBuffer(),
            m_meta="",
        )

    def consider_candidate(
        self,
        candidate_skill: str,
        score_on_sel: float,
        gate_strict: bool = True,
    ) -> Literal["accept_and_promote_best", "accept_only", "reject"]:
        """Algorithm 1 lines 20-27.

        Returns:
          - "accept_and_promote_best" if score > score_cur AND > score_best
          - "accept_only"             if score > score_cur but not > score_best
          - "reject"                  otherwise
        """
        cmp_cur = (
            score_on_sel > self.score_cur if gate_strict
            else score_on_sel >= self.score_cur
        )
        if not cmp_cur:
            return "reject"
        self.s_cur = candidate_skill
        self.score_cur = score_on_sel
        cmp_best = (
            score_on_sel > self.score_best if gate_strict
            else score_on_sel >= self.score_best
        )
        if cmp_best:
            self.s_best = candidate_skill
            self.score_best = score_on_sel
            return "accept_and_promote_best"
        return "accept_only"

    # ---- Cache helper --------------------------------------------------- #

    def cached_or_eval(
        self,
        candidate_skill: str,
        eval_fn: Callable[[str], float],
    ) -> tuple[float, bool]:
        """Return (score, was_cached). eval_fn is called only on miss."""
        h = skill_hash(candidate_skill)
        cached = self.C.get(h)
        if cached is not None:
            return cached, True
        score = eval_fn(candidate_skill)
        self.C.put(h, score)
        return score, False


# --------------------------------------------------------------------------- #
# Step + epoch result records (for ITERATION_LOG.md / SCORE_REPORT.md)        #
# --------------------------------------------------------------------------- #


@dataclass
class StepResult:
    epoch: int
    step: int
    Lt_eff: int
    n_failures: int
    n_successes: int
    n_proposed_edits: int
    n_selected_edits: int
    n_applied_edits: int
    score_cand: float
    score_cur_before: float
    score_cur_after: float
    decision: Literal["accept_and_promote_best", "accept_only", "reject"]
    cand_hash: str
    cache_hit: bool
    wall_seconds: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class EpochResult:
    epoch: int
    n_steps: int
    n_accepted: int
    n_rejected: int
    n_promoted_to_best: int
    slow_update_applied: bool
    slow_update_accepted: Optional[bool]  # None if slow update wasn't run
    meta_skill_updated: bool
    score_cur_at_epoch_end: float
    score_best_at_epoch_end: float
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d


@dataclass
class TrainingRun:
    """Top-level record of one optimization run; serialized to ITERATION_LOG.md."""

    capability_id: str
    config: OptimizerConfig
    initial_skill_hash: str
    initial_score_sel: float
    epochs: list[EpochResult] = field(default_factory=list)
    final_test_score: Optional[float] = None
    final_skill_hash: Optional[str] = None
    final_skill_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "config": dataclasses.asdict(self.config),
            "initial_skill_hash": self.initial_skill_hash,
            "initial_score_sel": self.initial_score_sel,
            "epochs": [e.to_dict() for e in self.epochs],
            "final_test_score": self.final_test_score,
            "final_skill_hash": self.final_skill_hash,
            "final_skill_path": self.final_skill_path,
        }

    def write_json(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
