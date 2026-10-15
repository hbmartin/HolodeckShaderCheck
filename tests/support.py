import contextlib
import json
import pathlib
import tempfile
from holodeck_shader_check.library import Library


def write(root, name, value):
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value, encoding="utf-8")


@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for folder in ("shaders", "snippets", "references"):
            (root / folder).mkdir()
        write(root, "index.json", {"defaultShaderID": "example", "shaders": ["example"]})
        write(root, "shared/common.metal", "// café 雪\n")
        write(root, "shared/fragment.metal", "return float4(clamp(color, 0.0, 1.0), 1.0);\n")
        write(root, "shaders/example/body.metal", "float3 shade(float2 p, float time, float2 pixel) { return float3(0.5); }\n")
        write(root, "shaders/example/metadata.json", {"id": "example", "name": "Example", "category": "PROCEDURAL", "description": "Fixture", "colors": [[0, 0, 0], [1, 1, 1]], "shared": ["common", "fragment"]})
        write(root, "authoring.json", {"schemaVersion": 1, "sourceRequirements": {"shared/common.metal": [], "shared/fragment.metal": [], "shaders/example/body.metal": []}, "upstreamRequirements": {}, "regressionProfiles": []})
        yield Library(root)
