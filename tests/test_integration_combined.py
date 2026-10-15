import json
import os
import shutil
import unittest
from integration_support import ROOT, fixture_root
from holodeck_shader_check.library import Library
from holodeck_shader_check.licenses import audit_notices


@unittest.skipUnless(ROOT and os.environ.get("HOLODECK_GPU_TESTS") == "1", "Set library root and opt into actual GPU checks")
class CombinedRecipeTests(unittest.TestCase):
    def test_combined_unlisted_recipe_compiles_and_renders(self):
        with fixture_root("shaders", "shared", "snippets", "references", "templates") as root:
            library = Library(root)
            destination = root / "shaders/combined-recipe"
            shutil.copytree(root / "templates/shader", destination)
            for file in destination.iterdir():
                file.write_text(file.read_text().replace("__ID__", "combined-recipe").replace("__NAME__", "Combined Recipe"))
            code = (root / "snippets/glowing-ribbons/code.metal").read_text()
            code += "\nfloat3 shade(float2 p, float t, float2 pixel) { float center = (noise2(float2(p.x * 3, t * 0.2)) - 0.5) * 0.4; return palette(p.x + t * 0.05) * hdGlowingRibbon(p.y, center, 0.02); }\n"
            (destination / "body.metal").write_text(code)
            config = library.configuration()
            config["sourceRequirements"]["shaders/combined-recipe/body.metal"] = []
            (root / "authoring.json").write_text(json.dumps(config))
            library.validate(); audit_notices(library)
            library.compile_source(library.assemble("shader", "combined-recipe"), "combined-recipe")
            output = library.render("shader", "combined-recipe", "0,3,10", 320, 180)
            images = sorted(output.glob("*.png"))
            self.assertEqual(len(images), 3)
            self.assertTrue(all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in images))
            self.assertEqual(len({p.read_bytes() for p in images}), 3)
            self.assertNotIn("combined-recipe", library.entries()[0]["shaders"])
