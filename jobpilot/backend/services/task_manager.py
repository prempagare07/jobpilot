from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Any


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}

    def create(self, name: str, awaitable: Awaitable[Any], task_id: str | None = None) -> str:
        task_id = task_id or str(uuid.uuid4())
        self._tasks[task_id] = {
            "task_id": task_id,
            "name": name,
            "status": "queued",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }

        async def runner() -> None:
            self._tasks[task_id]["status"] = "running"
            self._tasks[task_id]["started_at"] = datetime.utcnow().isoformat() + "Z"
            try:
                result = await awaitable
                self._tasks[task_id]["status"] = "succeeded"
                self._tasks[task_id]["result"] = to_plain(result)
            except Exception as exc:
                self._tasks[task_id]["status"] = "failed"
                self._tasks[task_id]["error"] = str(exc)
            finally:
                self._tasks[task_id]["finished_at"] = datetime.utcnow().isoformat() + "Z"

        asyncio.create_task(runner())
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def list(self) -> list[dict[str, Any]]:
        return list(self._tasks.values())


def to_plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


task_registry = TaskRegistry()
