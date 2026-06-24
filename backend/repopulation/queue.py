"""Repopulation job queue — `submit(seed)` seam (main-thread code).

InProcessQueue runs jobs locally now; an SqsQueue implementing the same interface drops in for
the deployed worker pool with no change to run.py. This is the "no n8n" native-orchestration point.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Job:
    seed: dict


class Queue(Protocol):
    def submit(self, seed: dict) -> None: ...
    def poll(self) -> Job | None: ...


class InProcessQueue:
    def __init__(self) -> None:
        self._jobs: deque[Job] = deque()

    def submit(self, seed: dict) -> None:
        self._jobs.append(Job(seed))

    def poll(self) -> Job | None:
        return self._jobs.popleft() if self._jobs else None
