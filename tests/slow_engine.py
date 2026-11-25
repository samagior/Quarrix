"""Slow fake engine, to test running in a separate process."""

import time
from pathlib import Path

from quarrix.model import Block, ExtractedDocument


class SlowEngine:
    name = "slow"

    def __init__(self, settings: dict):
        self.delay = settings.get("delay", 0.5)

    def extract(self, path: Path) -> ExtractedDocument:
        time.sleep(self.delay)
        return ExtractedDocument("slow", 1, "slow", blocks=[Block("paragraph", 1, "ok")])


def make(settings: dict) -> SlowEngine:
    return SlowEngine(settings)
