from datetime import UTC, datetime, timedelta
from threading import Lock


_lock = Lock()
_progress: dict[str, dict[str, str]] = {}
_retention = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(UTC)


def _cleanup(now: datetime) -> None:
    expired = [
        key
        for key, value in _progress.items()
        if now - datetime.fromisoformat(value["updated_at"]) > _retention
    ]
    for key in expired:
        _progress.pop(key, None)


def update_progress(progress_id: str | None, stage: str, status: str = "running") -> None:
    if not progress_id:
        return
    now = _now()
    with _lock:
        _cleanup(now)
        _progress[progress_id] = {
            "progress_id": progress_id,
            "stage": stage,
            "status": status,
            "updated_at": now.isoformat(),
        }


def claim_progress(progress_id: str | None) -> bool:
    if not progress_id:
        return True
    now = _now()
    with _lock:
        _cleanup(now)
        if progress_id in _progress:
            return False
        _progress[progress_id] = {
            "progress_id": progress_id,
            "stage": "starting",
            "status": "running",
            "updated_at": now.isoformat(),
        }
        return True


def get_progress(progress_id: str) -> dict[str, str] | None:
    now = _now()
    with _lock:
        _cleanup(now)
        value = _progress.get(progress_id)
        return dict(value) if value else None
