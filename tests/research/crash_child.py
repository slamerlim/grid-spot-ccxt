"""Test-only process death before research result publication."""

import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

from grid.research import artifacts
from grid.research.config import load
from grid.research.replay import run


def crash(descriptor: int) -> None:
    os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    config, output = (Path(arg) for arg in sys.argv[1:])
    result = run(load(config))
    with patch("grid.research.artifacts.os.fsync", crash):
        artifacts.publish(output, result)
