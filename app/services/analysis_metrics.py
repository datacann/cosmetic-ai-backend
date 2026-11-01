from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from threading import Lock

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import AnalysisMetric
from app.models.full_analysis import FullProductAnalysisResponse
from app.services.openai_usage import begin_usage_capture, finish_usage_capture


@dataclass
class AnalysisMetricsCollector:
    request_key: str
    flow_type: str
    started_at: float = field(default_factory=monotonic)
    current_stage: str | None = None
    stage_started_at: float | None = None
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)
    _usage_token: object = field(init=False, repr=False)
    _model_usage: dict | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._usage_token = begin_usage_capture()

    def model_usage(self) -> dict:
        if self._model_usage is None:
            self._model_usage = finish_usage_capture(self._usage_token)
        return self._model_usage

    def stage(self, name: str) -> None:
        now = monotonic()
        with self._lock:
            self._close_stage(now)
            self.current_stage = name
            self.stage_started_at = now

    def snapshot(self) -> tuple[int, dict[str, int], str | None]:
        now = monotonic()
        with self._lock:
            durations = dict(self.stage_durations_ms)
            if self.current_stage and self.stage_started_at is not None:
                durations[self.current_stage] = durations.get(self.current_stage, 0) + round((now - self.stage_started_at) * 1000)
            return round((now - self.started_at) * 1000), durations, self.current_stage

    def _close_stage(self, now: float) -> None:
        if self.current_stage and self.stage_started_at is not None:
            elapsed = round((now - self.stage_started_at) * 1000)
            self.stage_durations_ms[self.current_stage] = self.stage_durations_ms.get(self.current_stage, 0) + elapsed


def save_analysis_metric(
    session: Session,
    *,
    collector: AnalysisMetricsCollector,
    user_id: str | None,
    status: str,
    response: FullProductAnalysisResponse | None = None,
    error: Exception | None = None,
) -> None:
    total_ms, stages, current_stage = collector.snapshot()
    warnings = response.warnings if response else []
    source_type = response.ingredient_acquisition.source.type if response and response.ingredient_acquisition else None
    metric = AnalysisMetric(
        analysis_id=response.analysis_id if response else None,
        user_id=user_id,
        request_key=collector.request_key,
        flow_type=collector.flow_type,
        status=status,
        total_ms=total_ms,
        stage_durations_ms=stages,
        formula_cache_used=source_type == "database",
        ingredient_cache_used=(
            "ingredient_analysis_loaded_from_cache" in warnings
            or "ingredient_items_loaded_from_cache" in warnings
        ),
        error_stage=current_stage if error else None,
        error_type=type(error).__name__ if error else None,
        model_usage=collector.model_usage(),
    )
    try:
        session.add(metric)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
