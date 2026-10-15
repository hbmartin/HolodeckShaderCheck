"""Geometry invariants and opt-in tests of actual Metal timing and guard behavior."""
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import unittest
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import patch

from . import shader_tool


def float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


FLOAT_FORMATS = {"float": ("<f", "<I"), "half": ("<e", "<H")}


def float_bits(value, precision="float"):
    float_format, bits_format = FLOAT_FORMATS[precision]
    return struct.unpack(bits_format, struct.pack(float_format, value))[0]


def bits_float(bits, precision="float"):
    float_format, bits_format = FLOAT_FORMATS[precision]
    return struct.unpack(float_format, struct.pack(bits_format, bits))[0]


# Positive literal, independently specified IEEE bits, and signs compiled on GPU.
# CPU tests exercise the unsigned, positive and negative forms of every row.
LITERAL_CASES = (
    ("1.0000000596046447f", 0x3f800000, ("",)),
    ("1.000000059604644775390625f", 0x3f800000, ("",)),
    ("1.0000000596046448f", 0x3f800001, ("", "-")),
    ("1.000000178813934326171875f", 0x3f800002, ("",)),
    ("0.0f", 0x00000000, ()),
    ("1e-50f", 0x00000000, ()),
    ("1e-400f", 0x00000000, ()),
    ("1e-999f", 0x00000000, ()),
    ("7.006492321624085e-46f", 0x00000000, ()),
    ("7.006492321624085354618647916449580656401309709382578858785341419448955413429303"
     "00743319094181060791015625e-46f", 0x00000000, ()),
    ("7.006492321624086e-46f", 0x00000001, ()),
    ("1e-45f", 0x00000001, ("",)),
    ("3e-45f", 0x00000002, ()),
    ("1.1754942106924411e-38f", 0x007fffff, ()),
    ("1.1754943508222875e-38f", 0x00800000, ()),
    ("340282346638528859811704183484516925440.0f", 0x7f7fffff, ()),
    ("340282356779733661637539395458142568447.0f", 0x7f7fffff, ()),
    ("3.4028235e38f", 0x7f7fffff, ("",)),
    ("1.0000000596046448F", 0x3f800001, ("",)),
    ("1.0004882812499999h", 0x3c00, ("",)),
    ("1.00048828125h", 0x3c00, ("",)),
    ("1.0004882812500001h", 0x3c01, ("", "-")),
    ("1.00146484375h", 0x3c02, ("",)),
    ("0.0h", 0x0000, ()),
    ("1e-50h", 0x0000, ()),
    ("1e-400h", 0x0000, ()),
    ("0.0000000298023223876953124h", 0x0000, ()),
    ("0.0000000298023223876953125h", 0x0000, ()),
    ("0.0000000298023223876953126h", 0x0001, ()),
    ("0.000000059604644775390625h", 0x0001, ("",)),
    ("0.000060975551605224609375h", 0x03ff, ()),
    ("0.00006103515625h", 0x0400, ()),
    ("65504.0h", 0x7bff, ("",)),
    ("65519.9999999999999999h", 0x7bff, ()),
    ("1.0004882812500001H", 0x3c01, ("",)),
)


FRAGMENT_CLAMP = "return float4(clamp(color, 0.0, 1.0), 1.0);"
FRAGMENT_COLOR = "float3 color = shade(p, u.time, in.position.xy);"
BOUNCE_CALL = "float3 centre = laneCentres[k] + hdBounceInBox(starts[k], speeds[k], bounceHalf, t);"
AVAILABLE_EXTENT = "kRoomHalf - abs(laneCentres[k]) - bound - kRoomClearance"
GEOMETRY_TOLERANCE = 1e-5
# Metal suffixes require a decimal point or exponent; plain integers remain valid.
FLOAT_LITERAL = r"[+-]?(?:(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)[fFhH]?|[+-]?\d+"


def require_one(count, purpose):
    if count != 1:
        raise AssertionError(f"{purpose}: expected 1 match, found {count}")


def replace_once(source, anchor, replacement, purpose):
    require_one(source.count(anchor), purpose)
    return source.replace(anchor, replacement, 1)


def sub_once(source, pattern, replacement, purpose):
    result, count = re.subn(pattern, replacement, source, flags=re.DOTALL)
    require_one(count, purpose)
    return result


def scene_match(source, pattern, name):
    matches = list(re.finditer(pattern, source, flags=re.DOTALL))
    require_one(len(matches), f"Scene data {name} (missing or unsupported declaration)")
    return matches[0]


def literal_value(part, name):
    """Round an exact decimal directly to the literal's Metal storage precision."""
    suffix = part[-1] if part[-1] in "fFhH" else ""
    if suffix in ("h", "H"):
        precision = "half"
        largest_bits, sign_bit, next_power = 0x7bff, 0x8000, 2.0**16
    else:
        precision = "float"
        largest_bits, sign_bit, next_power = 0x7f7fffff, 0x80000000, 2.0**128
    range_message = f"Invalid scene data {name}: {precision} literal out of range ({part})"
    try:
        decimal_value = Decimal(part[:-1] if suffix else part)
    except InvalidOperation as error:
        raise AssertionError(range_message) from error
    magnitude = decimal_value.copy_abs()

    def decode(bits):
        return bits_float(bits, precision)

    # Adjacent binary16/32 values and their midpoints are exact in binary64.
    # At the overflow midpoint, ties-to-even selects the next power (infinity).
    overflow_midpoint = Decimal.from_float((decode(largest_bits) + next_power) / 2)
    if magnitude >= overflow_midpoint:
        raise AssertionError(range_message)

    # Positive finite IEEE bit patterns increase monotonically with their values.
    lower, upper = 0, largest_bits
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if Decimal.from_float(decode(middle)) <= magnitude:
            lower = middle
        else:
            upper = middle - 1
    if lower < largest_bits:
        midpoint = Decimal.from_float((decode(lower) + decode(lower + 1)) / 2)
        if magnitude > midpoint or (magnitude == midpoint and lower & 1):
            lower += 1
    return decode(lower | (sign_bit if decimal_value.is_signed() else 0))


def values(text, count, name):
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != count or any(not re.fullmatch(FLOAT_LITERAL, part) for part in parts):
        raise AssertionError(f"Invalid scene data {name}: expected {count} numeric literals")
    return tuple(literal_value(part, name) for part in parts)


def array_pattern(kind, name):
    return rf"\b{kind}\s+{re.escape(name)}\s*\[4\]\s*=\s*\{{(.*?)\}}\s*;"


def vectors(source, name):
    body = scene_match(source, array_pattern("float3", name), name).group(1)
    vector = r"\bfloat3\s*\(([^()]*)\)"
    entries = re.findall(vector, body)
    residue = re.sub(vector, "V", body)
    if len(entries) != 4 or not re.fullmatch(r"\s*V\s*,\s*V\s*,\s*V\s*,\s*V\s*", residue):
        raise AssertionError(f"Invalid scene data {name}: expected 4 float3 vectors")
    return [values(entry, 3, name) for entry in entries]


def scalar_array(source, name):
    return values(scene_match(source, array_pattern("float", name), name).group(1), 4, name)


def scalar_constant(source, name):
    match = scene_match(source, rf"\bconstant\s+float\s+{re.escape(name)}\s*=\s*([^;]+);", name)
    return values(match.group(1), 1, name)[0]


def vector_constant(source, name):
    match = scene_match(source, rf"\bconstant\s+float3\s+{re.escape(name)}\s*=\s*float3\s*\(([^()]*)\)\s*;", name)
    return values(match.group(1), 3, name)


def unclamped(source):
    return replace_once(source, FRAGMENT_CLAMP, "return float4(color, 1.0);", "Remove fragment clamp")


def invalid_lane(source, kind, extent):
    lanes = vectors(source, "laneHalves")
    if kind == "lane":
        lanes[2] = (lanes[2][0], extent, lanes[2][2])
        declaration = "float3 laneHalves[4] = {" + ", ".join(
            "float3(" + ", ".join(str(value) for value in lane) + ")" for lane in lanes) + "};"
        return sub_once(source, array_pattern("float3", "laneHalves"), lambda _: declaration, "Mutate octahedron lane")
    if kind == "available":
        return replace_once(source, AVAILABLE_EXTENT,
                            f"(k == 2 ? float3({lanes[2][0]}, {extent}, {lanes[2][2]}) : {AVAILABLE_EXTENT})",
                            "Mutate available extent")
    raise AssertionError(f"Unknown invalid extent fixture: {kind}")


class SceneGeometryTests(unittest.TestCase):
    def test_effective_lanes_fit_with_positive_extents_and_clearance(self):
        source = (self.library.root / "shaders/holodeck-grid/body.metal").read_text()
        room = vector_constant(source, "kRoomHalf")
        clearance = scalar_constant(source, "kRoomClearance")
        scale = scalar_constant(source, "kRotatedBoundScale")
        # Only the documented scene expressions are supported; never silently model
        # a different runtime bound or containment formula.
        scene_match(source, re.escape("float bound = (k == 1 || k == 2) ? sizes[k] * kRotatedBoundScale : sizes[k];"), "rotated bound expression")
        scene_match(source, re.escape(f"float3 bounceHalf = min(laneHalves[k], {AVAILABLE_EXTENT});"), "effective extent expression")
        self.assertGreater(clearance, 0)
        self.assertAlmostEqual(scale, math.sqrt(3), delta=1e-7)
        centres, halves, starts = (vectors(source, name) for name in ("laneCentres", "laneHalves", "starts"))
        sizes = scalar_array(source, "sizes")
        for k in range(4):
            self.assertGreater(sizes[k], 0)
            runtime_radius = sizes[k] * (scale if k in (1, 2) else 1)
            radius = sizes[k] * (math.sqrt(3) if k in (1, 2) else 1)
            for axis in range(3):
                effective = min(halves[k][axis], room[axis] - abs(centres[k][axis]) - runtime_radius - clearance)
                with self.subTest(object=k, axis=axis):
                    self.assertGreater(room[axis], 0)
                    self.assertGreater(effective, 0)
                    self.assertLessEqual(abs(starts[k][axis]), effective)
                    self.assertLessEqual(abs(centres[k][axis]) + effective + radius + clearance,
                                         room[axis] + GEOMETRY_TOLERANCE)


class MetalProbeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.probe = cls.library.gpu_command("probe")

    def run_probe(self, label, mode, source, samples):
        source_path, samples_path, output_path = (self.root / f"{label}{suffix}"
                                                 for suffix in (".metal", ".json", ".result.json"))
        if mode == "render":
            self.assertTrue(samples, f"{label}: no render samples")
        source_path.write_text(source)
        samples_path.write_text(json.dumps(samples))
        shader_tool.run([*self.probe, mode, str(source_path), str(samples_path), str(output_path)],
                        operation="probe", label=label)
        result = json.loads(output_path.read_text())
        self.assertIsInstance(result, dict, f"{label}: probe result must be an object")
        if mode == "render":
            self.assertIsInstance(result.get("frames"), list, f"{label}: missing render frames")
            self.assertEqual(len(result["frames"]), len(samples), f"{label}: render frame count")
        return result

    def assert_render_equal(self, actual, expected):
        self.assertEqual(len(actual["frames"]), len(expected["frames"]))
        for a, e in zip(actual["frames"], expected["frames"]):
            self.assertEqual(a["nonfinite"], 0)
            self.assertEqual(Path(a["path"]).read_bytes(), Path(e["path"]).read_bytes())


class MetalSceneRegressionTests(MetalProbeCase):
    def contract_source(self):
        return (self.library.read_source(self.library.path("shared/common.metal"))
                + "\nfloat3 shade(float2 p, float time, float2 pixel) { return float3(0.5); }\n"
                + self.library.read_source(self.library.path("shared/fragment.metal")))

    def test_shared_uniforms_deliver_resolution_and_time(self):
        source = replace_once(unclamped(self.contract_source()), FRAGMENT_COLOR,
                              "float3 color = float3(u.resolution, u.time);", "Expose Metal uniform values")
        source += '\nstatic_assert(sizeof(ShaderUniforms) == 16, "ShaderUniforms must match the 16-byte host buffer");\n'
        self.library.compile_source(source, "shared-uniform-contract")
        result = self.run_probe("shared-uniforms", "render", source, [0, 3.25])
        for frame, time in zip(result["frames"], (0, 3.25)):
            pixels = Path(frame["path"]).read_bytes()
            self.assertEqual(len(pixels), 160 * 90 * 16)
            self.assertEqual(pixels, struct.pack("4f", 160, 90, time, 1) * (160 * 90))

    def test_nan_diagnostic_before_actual_fragment_clamp(self):
        source = replace_once(self.contract_source(), FRAGMENT_COLOR,
                              "float3 color = float3(as_type<float>(0x7fc00000u));", "Inject NaN diagnostic")
        raw = self.run_probe("shared-nan-raw", "render", unclamped(source), [0])
        self.assertEqual(raw["frames"][0]["nonfinite"], 160 * 90 * 3)
        clamped = self.run_probe("shared-nan-clamped", "render", source, [0])
        self.assertEqual(clamped["frames"][0]["nonfinite"], 0)

    def test_scene_literals_match_compiled_metal_constants(self):
        cases = []
        for literal, expected, gpu_signs in LITERAL_CASES:
            sign_bit = 0x8000 if literal.endswith(("h", "H")) else 0x80000000
            cases.extend((sign + literal, expected | (sign_bit if sign == "-" else 0)) for sign in gpu_signs)
        branches = []
        parsed = []
        for index, (literal, _) in enumerate(cases):
            half = literal.endswith(("h", "H"))
            expression = f"uint(as_type<ushort>({literal}))" if half else f"as_type<uint>({literal})"
            branches.append(f"    case {index}: seeds[i] = {expression}; break;")
            value = values(literal, 1, "compiled literal")[0]
            parsed.append(float_bits(value, "half" if half else "float"))
        source = """
#include <metal_stdlib>
using namespace metal;
kernel void regressionProbe(device const float2 *samples [[buffer(0)]],
                            device uint *seeds [[buffer(1)]],
                            uint i [[thread_position_in_grid]]) {
    switch (i) {
""" + "\n".join(branches) + "\n    }\n}\n"
        result = self.run_probe("literal-rounding", "compute", source, [[0, 0]] * len(cases))
        self.assertEqual(result["seeds"], [bits for _, bits in cases])
        self.assertEqual(result["seeds"], parsed)
        self.library.compile_source(source, "literal-rounding")


    def test_actual_matrix_seed_sequence_through_one_year(self):
        source = self.library.shader_entry("matrix-corridor")[1]
        source += """
kernel void regressionProbe(device const float2 *samples [[buffer(0)]],
                            device uint *seeds [[buffer(1)]],
                            uint i [[thread_position_in_grid]]) {
    seeds[i] = hdMxGlyphSeed(hdMxGlyphPhase(samples[i].x), uint(samples[i].y));
}
"""
        centres = [0, 3, 10, 1820, 3740, 86400, 19 * 86400, 20 * 86400, 78 * 86400, 365 * 86400]
        centres += [cycle * 131072 - 100 for cycle in range(1, 241)]
        samples = [[max(0, time + dt), steps]
                   for time in centres for dt in (-0.5, -0.125, 0, 0.125, 0.5)
                   for steps in range(1, 81)]
        # Adjacent representable float inputs around fractional tick boundaries
        # catch a product that rounds up before the mathematical seed transition.
        for steps in range(1, 81):
            first_tick = math.ceil(100 * steps / 32)
            for tick in range(first_tick, first_tick + 400):
                time = float32(tick * 32 / steps - 100)
                bits = float_bits(time)
                for adjacent in (max(0, bits - 1), bits, bits + 1):
                    samples.append([bits_float(adjacent), steps])
        result = self.run_probe("matrix-seeds", "compute", source, samples)
        # Independent, unbounded double-precision oracle, using the identical float input/addition.
        # The oracle does not reproduce the implementation's wrapped phase or split arithmetic.
        expected = [math.floor(float32(float32(time) + 100) * steps / 32) % 4096
                    for time, steps in samples]
        self.assertEqual(result["seeds"], expected)
        print(f"Metal seed oracle: {len(samples)} cases on {result['device']}", flush=True)

    def test_nonpositive_lane_is_skipped_before_bounce_division(self):
        source = unclamped(self.library.shader_entry("holodeck-grid")[1])
        # A sentinel immediately before the real bounce call proves invalid geometry
        # cannot reach it, even if subsequent intersection math would hide a NaN.
        source = replace_once(source, BOUNCE_CALL,
                              "if (any(bounceHalf <= 0.0)) return float3(100.0);\n        " + BOUNCE_CALL,
                              "Insert pre-bounce sentinel")
        times = [0, 3, 1240.6, 86400]
        # Removing object 2 is an independent rendering oracle for an invalid lane.
        reference = replace_once(source, "for (int k = 0; k < 4; ++k) {",
                                 "for (int k = 0; k < 4; ++k) { if (k == 2) continue;",
                                 "Remove octahedron reference object")
        expected = self.run_probe("without-object", "render", reference, times)
        for kind in ("lane", "available"):
            for extent in (0.0, -1.0):
                with self.subTest(kind=kind, extent=extent):
                    variant = invalid_lane(source, kind, extent)
                    actual = self.run_probe(f"invalid-{kind}-{extent}", "render", variant, times)
                    self.assert_render_equal(actual, expected)
                    # Every fixture must distinguish one invalid axis from all axes,
                    # and catch guard removal even if later math conceals invalid values.
                    for mutation, replacement in (("no-guard", ""), ("all", "if (all(bounceHalf <= 0.0)) continue;")):
                        with self.subTest(mutation=mutation):
                            mutated = replace_once(variant, "if (any(bounceHalf <= 0.0)) continue;", replacement,
                                                   "Mutate real geometry guard negative control")
                            control = self.run_probe(f"invalid-{kind}-{extent}-{mutation}", "render", mutated, times)
                            with self.assertRaises(AssertionError):
                                self.assert_render_equal(control, expected)
        print(f"Metal invalid lanes: zero/negative authored and available extents skip the bounce call on {actual['device']}", flush=True)

    def test_raw_rgb_is_finite_at_wraps_and_long_uptime(self):
        times = [0, 3, 10, 1819.875, 1820, 1820.125, 3740,
                 130971.875, 130972, 130972.125, 86400, 20 * 86400, 78 * 86400, 365 * 86400]
        for scene in (["matrix-corridor"] if "matrix-corridor-v1" in self.library.configuration()["regressionProfiles"] else []) + (["holodeck-grid"] if "holodeck-grid-v1" in self.library.configuration()["regressionProfiles"] else []):
            source = unclamped(self.library.shader_entry(scene)[1])
            result = self.run_probe(scene + "-finite", "render", source, times)
            self.assertTrue(all(frame["nonfinite"] == 0 for frame in result["frames"]), scene)




def run_profiles(library, gpu=False):
    """Run immutable checker code against the caller's explicitly selected assets."""
    profiles = library.configuration()["regressionProfiles"]
    suite = unittest.TestSuite()
    if "holodeck-grid-v1" in profiles:
        case = type("GeometryProfile", (SceneGeometryTests,), {"library": library})
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    if gpu:
        methods = []
        if "shared-runtime-v1" in profiles:
            methods += ["test_shared_uniforms_deliver_resolution_and_time", "test_nan_diagnostic_before_actual_fragment_clamp", "test_scene_literals_match_compiled_metal_constants"]
        if "holodeck-grid-v1" in profiles:
            methods += ["test_nonpositive_lane_is_skipped_before_bounce_division"]
        if "matrix-corridor-v1" in profiles:
            methods += ["test_actual_matrix_seed_sequence_through_one_year"]
        if any(p in profiles for p in ("holodeck-grid-v1", "matrix-corridor-v1")):
            methods += ["test_raw_rgb_is_finite_at_wraps_and_long_uptime"]
        case = type("GPUProfile", (MetalSceneRegressionTests,), {"library": library})
        suite.addTests(case(method) for method in methods)
    result = unittest.TestResult()
    suite.run(result)
    if not result.wasSuccessful():
        diagnostics = "\n".join(f"{case}: {trace}" for case, trace in result.errors + result.failures)
        # Missing backend / SDK is an operational failure, not a shader repair prompt.
        from .errors import ToolingError
        if any("ToolingError:" in trace for _, trace in result.errors):
            raise ToolingError(diagnostics)
        raise ValueError(diagnostics)
    return result.testsRun
