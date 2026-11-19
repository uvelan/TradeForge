"""Bounded cooperative runtime for a single evaluation request."""
from time import monotonic

from .analysis_evidence import ResourceLimit

DEADLINE_SECONDS = 20


def deadline(*, clock=monotonic):
    stop = clock() + DEADLINE_SECONDS

    def check():
        if clock() >= stop:
            raise ResourceLimit("Strategy evaluation cooperative deadline exceeded")

    return check
