#!/usr/bin/env python3
"""Run real-library coverage, failing instead of silently skipping CPU integration tests."""
import argparse
import os
from pathlib import Path
import sys
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def run_suite(suite, gpu=False, stream=None):
    selected = unittest.TestSuite(case for case in cases(suite)
                                 if gpu or case.__class__.__module__ != "test_integration_combined")
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(selected)
    skipped = [case.id() for case, _ in result.skipped
               if case.__class__.__module__.startswith("test_integration_")]
    if skipped:
        print("Required integration tests skipped: " + ", ".join(skipped), file=stream or sys.stderr)
    return result.wasSuccessful() and not skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true", help="Also require actual GPU composition checks")
    args = parser.parse_args()
    library = os.environ.get("HOLODECK_LIBRARY_ROOT")
    if not library or not (Path(library) / "authoring.json").is_file():
        parser.error("Set HOLODECK_LIBRARY_ROOT to an explicit shader library root")
    if args.gpu:
        os.environ["HOLODECK_GPU_TESTS"] = "1"
    else:
        os.environ.pop("HOLODECK_GPU_TESTS", None)
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "src"))
    suite = unittest.defaultTestLoader.discover(str(project / "tests"))
    return 0 if run_suite(suite, gpu=args.gpu) else 1


if __name__ == "__main__":
    raise SystemExit(main())
