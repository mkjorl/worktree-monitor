"""Collect redistribution notices from the exact runtime used for this build."""
import ast
from importlib import metadata
from pathlib import Path
import sys
import sysconfig

sections = ["Worktree Monitor bundled third-party software\n"]
license_candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"]
license_candidates += [parent / "LICENSE" for parent in Path(sys.base_prefix).resolve().parents]
license_file = next((path for path in license_candidates if path.is_file()), None)
if license_file is None:
    raise SystemExit("Cannot find Python LICENSE.txt; do not distribute without its license.")
runtime_notices = Path(__file__).with_name("python-runtime-notices.rst")
if "cpython-3.12.10-macos" in sys.base_prefix:
    sections.append("Astral python-build-standalone 20250529\n" + runtime_notices.read_text())
sections.append("Python " + sys.version + "\n" + license_file.read_text())
for name in ("PyInstaller", "python-dotenv"):
    distribution = metadata.distribution(name)
    files = [path for path in distribution.files or () if any(word in path.name.lower() for word in ("license", "copying", "notice"))]
    if not files:
        raise SystemExit(f"Missing license for {name}")
    for path in files:
        sections.append(f"{name} {distribution.version} — {path.name}\n" + Path(distribution.locate_file(path)).read_text())
# PyInstaller's build manifest records source libraries before copying them.
# Include licenses shipped next to those libraries (for example OpenSSL/liblzma).
seen = {license_file.resolve()}
def collect_library_notices(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            collect_library_notices(item)
    elif isinstance(value, str) and value.startswith("/") and (value.endswith(".dylib") or value.endswith(".so")):
        for parent in Path(value).resolve().parents:
            if parent == Path("/"):
                break
            candidates = [*parent.glob("LICENSE*"), *parent.glob("COPYING*")]
            for path in candidates:
                if path.is_file() and path.resolve() not in seen:
                    seen.add(path.resolve())
                    sections.append(f"Bundled library: {Path(value).name} — {path.name}\n" + path.read_text())
            if candidates:
                break
if len(sys.argv) > 2:
    collect_library_notices(ast.literal_eval(Path(sys.argv[2]).read_text()))
Path(sys.argv[1]).write_text("\n\n".join(sections))
