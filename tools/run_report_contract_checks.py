"""Run report-contract regressions with external effects prohibited.

Rendering/export tests that launch a browser are intentionally not selected.
Model, research, image and export interfaces in these suites use injected fakes.
"""
from pathlib import Path
from contextlib import contextmanager
import plistlib
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SUITES = (
    "tests.test_standard_length_recovery",
    "tests.test_rag_bridge",
    "tests.test_generation_continuity",
    "tests.test_generation_workflows",
    "tests.test_report_publication_contract",
    "tests.test_simplified_report_mode",
    "tests.test_report_contract_runner",
    "tests.test_generation_retry_context",
    "tests.test_generation_retry_sqlalchemy",
)


@contextmanager
def empty_system_font_catalog():
    """Mock dependency-only font discovery; never execute these commands.

    Matplotlib's cold import asks fontconfig (or macOS system_profiler) for
    system font paths. These contract tests use its bundled fonts and do not
    test the host's font inventory. Unknown commands retain the process guard.
    """
    check_output = subprocess.check_output
    catalog = {
        ("fc-list", "--help"): b"--format",
        ("fc-list", "--format=%{file}\\n"): b"",
        ("system_profiler", "-xml", "SPFontsDataType"):
            plistlib.dumps([{"_items": []}]),
    }

    def font_query(command, *args, **kwargs):
        key = tuple(command) if isinstance(command, (list, tuple)) else None
        if not args and not kwargs and key in catalog:
            return catalog[key]
        return check_output(command, *args, **kwargs)

    with patch.object(subprocess, "check_output", font_query):
        yield


def initialize_matplotlib():
    # This executes inside the main network/process guard, before suite import.
    # Only the explicit font-list interfaces are mocked, including on cold CI.
    with empty_system_font_catalog():
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot  # noqa: F401


def main():
    attempted = []

    def forbidden(*_args, **_kwargs):
        attempted.append("external_effect")
        raise AssertionError("Offline contract checks prohibit network and process execution")

    with patch.object(socket, "getaddrinfo", forbidden), \
         patch.object(socket.socket, "connect", forbidden), \
         patch.object(socket.socket, "connect_ex", forbidden), \
         patch.object(subprocess, "Popen", forbidden):
        initialize_matplotlib()
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if attempted:
        # A fallback must not hide an accidental unmocked external call.
        print(f"Offline contract violated: {len(attempted)} external call(s) blocked.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
