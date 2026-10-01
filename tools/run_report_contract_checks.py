"""Run report-contract regressions with external effects prohibited.

Rendering/export tests that launch a browser are intentionally not selected.
Model, research, image and export interfaces in these suites use injected fakes.
"""
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SUITES = (
    "tests.test_standard_length_recovery",
    "tests.test_rag_bridge",
    "tests.test_generation_workflows",
    "tests.test_report_publication_contract",
    "tests.test_simplified_report_mode",
)


def main():
    attempted = []

    def forbidden(*_args, **_kwargs):
        attempted.append("external_effect")
        raise AssertionError("Offline contract checks prohibit network and process execution")

    with patch.object(socket, "getaddrinfo", forbidden), \
         patch.object(socket.socket, "connect", forbidden), \
         patch.object(socket.socket, "connect_ex", forbidden), \
         patch.object(subprocess, "Popen", forbidden):
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if attempted:
        # A fallback must not hide an accidental unmocked external call.
        print(f"Offline contract violated: {len(attempted)} external call(s) blocked.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
