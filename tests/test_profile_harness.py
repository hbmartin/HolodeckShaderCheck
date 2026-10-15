from holodeck_shader_check.profiles import *
del SceneGeometryTests, MetalSceneRegressionTests
from holodeck_shader_check.library import Library
from support import fixture


class RegressionHarnessTests(unittest.TestCase):
    def setUp(self):
        context = fixture()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        MetalProbeCase.library = self.library

    def test_scene_parsers_accept_metal_suffixes_and_half_rounding(self):
        half = bits_float(0x2a66, "half")
        for literal, expected in (("0.05f", float32(0.05)), (".05F", float32(0.05)), ("5e-2f", float32(0.05)),
                                  ("0.05", float32(0.05)), ("0.05h", half), ("5e-2H", half), ("1.F", 1.0),
                                  ("2", 2.0), ("3.4028235e38f", float32(3.4028235e38)),
                                  ("1e-50f", 0.0), ("1e-400", 0.0), ("1e-50h", 0.0)):
            with self.subTest(literal=literal):
                self.assertEqual(scalar_constant(f"constant float clearance = {literal};", "clearance"), expected)
                self.assertEqual(vector_constant(f"constant float3 room = float3({literal}, 2, 3);", "room"), (expected, 2, 3))
                self.assertEqual(scalar_array(f"float sizes[4] = {{{literal}, 2, 3, 4}};", "sizes"), (expected, 2, 3, 4))
                source = "float3 lanes[4] = {" + ", ".join([f"float3({literal}, 2, 3)"] * 4) + "};"
                self.assertEqual(vectors(source, "lanes"), [(expected, 2, 3)] * 4)
        for literal in ("1f", "1h", "0.05ff", "0.05hf", "1eH", "1e999f", "1e10h"):
            with self.subTest(literal=literal), self.assertRaisesRegex(AssertionError, "Invalid scene data"):
                scalar_constant(f"constant float clearance = {literal};", "clearance")

    def test_scene_literals_round_exactly_at_midpoints_and_boundaries(self):
        for literal, expected, _ in LITERAL_CASES:
            precision = "half" if literal.endswith(("h", "H")) else "float"
            sign_bit = 0x8000 if precision == "half" else 0x80000000
            for sign in ("", "-", "+"):
                with self.subTest(literal=sign + literal):
                    value = values(sign + literal, 1, "rounding")[0]
                    self.assertEqual(float_bits(value, precision), expected | (sign_bit if sign == "-" else 0))

    def test_scene_parsers_reject_overflow_with_original_literal(self):
        cases = {
            "float": ("1e39f", "1e39F", "1e39", "3.4028236e38f", "1e999f", "1e999F", "1e999",
                      "340282356779733661637539395458142568448.0f",
                      "340282356779733661637539395458142568449.0f"),
            "half": ("1e10h", "1e999h", "1e999H", "65520.0h", "65520.0000000000000001h"),
        }
        for precision, literals in cases.items():
            for literal in literals:
                for sign in ("", "-", "+"):
                    part = sign + literal
                    with self.subTest(literal=part):
                        with self.assertRaises(AssertionError) as error:
                            scalar_constant(f"constant float clearance = {part};", "clearance")
                        self.assertEqual(str(error.exception),
                                         f"Invalid scene data clearance: {precision} literal out of range ({part})")

    def test_scene_parsers_label_exponents_beyond_decimal_limits(self):
        for suffix in ("", "f", "F", "h", "H"):
            precision = "half" if suffix in ("h", "H") else "float"
            for exponent_sign in ("+", "-"):
                for sign in ("", "-", "+"):
                    part = f"{sign}1e{exponent_sign}99999999999999999999{suffix}"
                    with self.subTest(literal=part):
                        with self.assertRaises(AssertionError) as error:
                            scalar_constant(f"constant float clearance = {part};", "clearance")
                        self.assertEqual(str(error.exception),
                                         f"Invalid scene data clearance: {precision} literal out of range ({part})")
                        self.assertIsInstance(error.exception.__cause__, InvalidOperation)

    def test_source_edits_reject_missing_and_duplicate_anchors(self):
        for edit in (lambda source: replace_once(source, "anchor", "replacement", "Fixture anchor"),
                     lambda source: sub_once(source, r"anchor", "replacement", "Fixture anchor")):
            self.assertEqual(edit("anchor"), "replacement")
            for source, count in (("absent", 0), ("anchor anchor", 2)):
                with self.subTest(source=source), self.assertRaisesRegex(AssertionError, f"Fixture anchor: expected 1 match, found {count}"):
                    edit(source)
        wrapper = (self.library.root / "shared/fragment.metal").read_text()
        drifted = replace_once(wrapper, "clamp(color,", "clamp( color,", "Simulate wrapper drift")
        with self.assertRaisesRegex(AssertionError, "Remove fragment clamp"):
            unclamped(drifted)

    def test_scene_parsers_reject_missing_malformed_and_wrong_length_data(self):
        vector_data = "float3 lanes[4] = {float3(1, 2, 3), float3(4, 5, 6), float3(7, 8, 9), float3(10, 11, 12)};"
        self.assertEqual(vectors(vector_data, "lanes")[2], (7, 8, 9))
        self.assertEqual(scalar_array("float sizes[4] = {1, 2, 3, 4};", "sizes"), (1, 2, 3, 4))
        fixtures = [
            (lambda s: vectors(s, "lanes"), ""),
            (lambda s: vectors(s, "lanes"), vector_data + vector_data),
            (lambda s: vectors(s, "lanes"), vector_data.replace("float3(1, 2, 3), ", "")),
            (lambda s: vectors(s, "lanes"), vector_data.replace("1, 2, 3", "1, 2")),
            (lambda s: vectors(s, "lanes"), vector_data.replace("1, 2, 3", "1, unknown, 3")),
            (lambda s: vectors(s, "lanes"), vector_data.replace("{", "{junk ")),
            (lambda s: scalar_array(s, "sizes"), ""),
            (lambda s: scalar_array(s, "sizes"), "float sizes[4] = {1, 2, 3};"),
            (lambda s: scalar_array(s, "sizes"), "float sizes[4] = {1, 2, NaN, 4};"),
            (lambda s: scalar_array(s, "sizes"), "float sizes[4] = {1, 2, 1e999, 4};"),
            (lambda s: vector_constant(s, "room"), ""),
            (lambda s: vector_constant(s, "room"), "constant float3 room = float3(1, 2);"),
            (lambda s: scalar_constant(s, "clearance"), ""),
            (lambda s: scalar_constant(s, "clearance"), "constant float clearance = 1 + 2;"),
        ]
        for parse, source in fixtures:
            with self.subTest(source=source), self.assertRaisesRegex(AssertionError, "[Ss]cene data"):
                parse(source)

    def test_dotted_labels_preserve_independent_probe_files(self):
        with tempfile.TemporaryDirectory() as directory:
            case = MetalProbeCase()
            case.root, case.probe = Path(directory), ["probe", "probe"]

            def simulate_probe(args, **kwargs):
                samples = json.loads(Path(args[4]).read_text())
                Path(args[5]).write_text(json.dumps({"frames": [{"time": time} for time in samples]}))

            with patch.object(subprocess, "run", side_effect=simulate_probe):
                for extent in (0.0, 0.5, 0.25):
                    case.run_probe(f"invalid-lane-{extent}", "render", f"source {extent}", [extent])
            for extent in (0.0, 0.5, 0.25):
                label = f"invalid-lane-{extent}"
                self.assertEqual((case.root / f"{label}.metal").read_text(), f"source {extent}")
                self.assertEqual(json.loads((case.root / f"{label}.json").read_text()), [extent])
                self.assertEqual(json.loads((case.root / f"{label}.result.json").read_text())["frames"][0]["time"], extent)

    def test_incomplete_render_results_fail_before_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            case = MetalProbeCase()
            case.root, case.probe = Path(directory), ["probe", "probe"]
            for frames in (None, [], [{}, {}]):
                def simulate_probe(args, **kwargs):
                    Path(args[5]).write_text(json.dumps({"frames": frames}))
                with self.subTest(frames=frames), patch.object(subprocess, "run", side_effect=simulate_probe), self.assertRaises(AssertionError):
                    case.run_probe("incomplete", "render", "source", [0])
            with patch.object(subprocess, "run") as run, self.assertRaisesRegex(AssertionError, "no render samples"):
                case.run_probe("empty", "render", "source", [])
            run.assert_not_called()

    def test_nonobject_probe_results_fail_with_label(self):
        with tempfile.TemporaryDirectory() as directory:
            case = MetalProbeCase()
            case.root, case.probe = Path(directory), ["probe", "probe"]
            for mode in ("render", "compute"):
                for payload in (None, [], 42):
                    def simulate_probe(args, **kwargs):
                        Path(args[5]).write_text(json.dumps(payload))
                    with self.subTest(mode=mode, payload=payload), patch.object(subprocess, "run", side_effect=simulate_probe):
                        with self.assertRaisesRegex(AssertionError, "bad-shape: probe result must be an object"):
                            case.run_probe("bad-shape", mode, "source", [0])

    def test_tool_resolution_failure_still_runs_class_cleanup(self):
        class FailingProbe(MetalProbeCase):
            def test_unused(self):
                self.fail("Tool resolution failure should prevent the test from running")

        result = unittest.TestResult()
        failure = ValueError("No usable shader tool")
        with patch.object(shader_tool, "command", side_effect=failure):
            unittest.TestSuite([FailingProbe("test_unused")]).run(result)
        self.assertEqual(result.testsRun, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(FailingProbe.root.exists())


