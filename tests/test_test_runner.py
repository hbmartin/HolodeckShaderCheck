import importlib.util
import io
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("run_tests", Path(__file__).resolve().parents[1] / "tools/run_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class IntegrationRunnerTests(unittest.TestCase):
    def skipped_case(self, module):
        case = type("UnavailableTests", (unittest.TestCase,), {
            "__module__": module,
            "test_unavailable": unittest.skip("fixture unavailable")(lambda self: None),
        })
        return case("test_unavailable")

    def test_cpu_integration_skip_fails_the_run(self):
        output = io.StringIO()
        self.assertFalse(runner.run_suite(unittest.TestSuite([self.skipped_case("test_integration_fixture")]), stream=output))
        self.assertIn("Required integration tests skipped", output.getvalue())

    def test_gpu_only_case_is_excluded_from_cpu_run_and_required_when_requested(self):
        suite = lambda: unittest.TestSuite([self.skipped_case("test_integration_combined")])
        self.assertTrue(runner.run_suite(suite(), stream=io.StringIO()))
        self.assertFalse(runner.run_suite(suite(), gpu=True, stream=io.StringIO()))
