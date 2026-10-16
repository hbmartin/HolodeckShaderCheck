# HolodeckShaderCheck

Portable authoring verification for [HolodeckShaders](https://github.com/hbmartin/HolodeckShaders). Python 3.9 or later; runtime dependencies are standard library only. Static checks require no Git, Xcode, Metal, or native executable. No code is imported from the library being checked.

Download a versioned zipapp or wheel and its `SHA256SUMS` from this repository's GitHub releases. Verify the checksum before use. Run the zipapp with isolated Python:

```sh
python3 -I holodeck-shader-check-1.0.3.pyz info --json
python3 -I holodeck-shader-check-1.0.3.pyz validate /path/to/HolodeckShaders --json
python3 -I holodeck-shader-check-1.0.3.pyz assemble /path/to/HolodeckShaders shader plasma --output /tmp/plasma.metal
```

Alternatively install the verified wheel into a virtual environment to obtain `holodeck-shader-check`. Compilation (`validate ROOT --compile`) requires macOS and the macOS/tvOS Metal SDKs. `validate ROOT --gpu` also compiles and runs the declared GPU profiles. `render ROOT shader|snippet ID` uses an installed major-version-1 `holodeck-shader-tool`; select it with `--shader-tool /absolute/tool`, `HOLODECK_SHADER_TOOL`, or PATH. Invalid explicit overrides never fall back. Render defaults are 1280×720 at times 0,3,10; `--width`, `--height`, `--times`, and `--output-dir` override them. Outputs default to `ROOT/.cache/library/<kind>-<id>/`.

Version probes disconnect stdin and time out after five seconds. Renders and probes disconnect stdin and use 120/300-second deadlines, respectively. Positive finite `HOLODECK_RENDER_TIMEOUT_SECONDS` and `HOLODECK_PROBE_TIMEOUT_SECONDS` override operation deadlines. Exit codes are 0 (pass), 1 (failed checks), and 2 (usage or unavailable tooling). `validate --json` emits one JSON result on stdout; diagnostics go to stderr.

## Library contract

The root `authoring.json` uses schema version 1 and exactly four fields: `schemaVersion`, `sourceRequirements`, `upstreamRequirements`, and `regressionProfiles`. Source requirements map every copyable source path to an explicit list of upstream IDs, including `[]` for original code. Upstream requirements map reference IDs to either `{license: "MIT", noticePath: "..."}` or the supported Creative Commons license and canonical `uri`. The inventories are independent of informational `reuse` links. Validation checks notices in independent files and assembled sources.

Versioned profiles are `shared-runtime-v1` (actual shared uniforms, clamp and literal behavior), `holodeck-grid-v1` (CPU containment and GPU invalid-lane/finite checks), and `matrix-corridor-v1` (seed and long-uptime checks). Unknown profiles and missing profile assets fail; no requested check silently skips. Ordinary validation runs CPU profiles, while `--gpu` adds actual GPU checks.

Source assembly preserves UTF-8 bytes and line endings. Shaders concatenate common, optional material, body, fragment without separators. Snippet harnesses retain their existing newline separators. When `templates/` is present, validation instantiates its shader, snippet and reference directories in disposable libraries and checks them with the same metadata, assembly, contract and provenance validators. Malformed metadata, missing required files, unresolved placeholders and symlinks fail validation. Libraries without templates remain supported. Template checks never modify the selected library or rewrite upstream originals. All complete shaders, including unlisted ones, snippet examples, and instantiated shader/snippet templates compile for both SDKs with `--compile`. Runtime delivery limits, previews, publication and editorial selection remain Holodeck responsibilities.

## Development and provenance

The initial implementation is extracted from `hbmartin/HolodeckShaders` commit `b2891b1c703f34beff323b189e0d3598578b1ae6`, preserving the existing source notices and regression oracles. Reference originals remain in the library and are not redistributed here. No new license grant is added by this extraction.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
HOLODECK_LIBRARY_ROOT=/path/to/HolodeckShaders python3 tools/run_tests.py
HOLODECK_LIBRARY_ROOT=/path/to/HolodeckShaders python3 tools/run_tests.py --gpu
python3 tools/build_release.py
```

The build command needs setuptools and wheel in its build environment. GitHub releases contain a wheel, deterministic zipapp and checksum inventory. Release artifacts are built from the tagged source commit. Integration tests require an explicit external library root; unit tests use synthetic assets. `tools/run_tests.py` fails if a required integration test skips. Its default CPU suite excludes the GPU-only combined-recipe composition test; `--gpu` requires that test too. Test and release CI run CPU coverage on Ubuntu with an immutable library fixture, and test CI covers Python 3.9 and 3.15. No macOS compilation, GPU execution or composition rendering runs in CI; run those checks explicitly on a Mac with the native CLI. GPU/backend tests retain independent numeric oracles and guard-removal negative controls.
