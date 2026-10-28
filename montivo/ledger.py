"""Run ledger: tokens, dollars and seconds per stage, written to ledger.json after every stage."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .engine.api import write_atomic


@dataclass
class Entry:
    stage: str
    model: str
    seconds: float
    cost_usd: float
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    frames: int = 0
    audio_s: float = 0.0
    status: str = "ok"
    note: str = ""


@dataclass
class Ledger:
    path: Path
    entries: list[Entry] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def resume(cls, path: Path) -> "Ledger":
        """A ledger that continues ledger.json when it exists (entries kept, wall clock continued)."""
        led = cls(path)
        if path.is_file():
            prev = json.loads(path.read_text())
            led.entries = [Entry(**e) for e in prev.get("entries", [])]
            led.started -= prev.get("wall_seconds", 0.0)
        return led

    def add(self, entry: Entry) -> Entry:
        with self._lock:
            self.entries.append(entry)
            self._write()
        return entry

    def total_cost(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 4)

    def summary(self) -> dict:
        by_stage: dict[str, dict] = {}
        for e in self.entries:
            s = by_stage.setdefault(e.stage, {"seconds": 0.0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0})
            s["seconds"] = round(s["seconds"] + e.seconds, 2)
            s["cost_usd"] = round(s["cost_usd"] + e.cost_usd, 4)
            s["tokens_in"] += e.tokens_in
            s["tokens_out"] += e.tokens_out
            s["calls"] += 1
        return {
            "total_cost_usd": self.total_cost(),
            "total_tokens_in": sum(e.tokens_in for e in self.entries),
            "total_tokens_out": sum(e.tokens_out for e in self.entries),
            "wall_seconds": round(time.time() - self.started, 1),
            "stages": by_stage,
            "entries": [asdict(e) for e in self.entries],
        }

    def _write(self) -> None:
        write_atomic(self.path, json.dumps(self.summary(), indent=2) + "\n")


class Timer:
    """with Timer() as t: ...; t.seconds"""

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc):
        self.seconds = round(time.perf_counter() - self.t0, 2)
        return False
