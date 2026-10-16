#!/usr/bin/env python3
"""Build deterministic zipapp, wheel, and checksums from the current source."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from holodeck_shader_check import __version__

out = root / "dist"
out.mkdir(exist_ok=True)
subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(out), str(root)], check=True)
archive = out / f"holodeck-shader-check-{__version__}.pyz"
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip:
    sources = [(str(path.relative_to(root / "src")), path.read_bytes()) for path in sorted((root / "src").rglob("*.py"))]
    sources.append(("__main__.py", b"from holodeck_shader_check.cli import main\nraise SystemExit(main())\n"))
    for name, data in sources:
        info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zip.writestr(info, data)
files = [archive, *sorted(out.glob(f"holodeck_shader_check-{__version__}-*.whl"))]
(out / "SHA256SUMS").write_text("".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in files))
print((out / "SHA256SUMS").read_text())
