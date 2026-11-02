"""
Checksum manifest of the published benchmark artefacts, so the hybrid work can
prove it never overwrote them: eval/results/, eval/results_v2/, eval/RESULTS.md
and the scenario files.

Text files are hashed with LF line endings, so a checkout with core.autocrlf
gives the same digest; images are hashed byte for byte.

    backend\\.venv\\Scripts\\python.exe eval\\manifest.py --write   # record eval/baseline_manifest.json
    backend\\.venv\\Scripts\\python.exe eval\\manifest.py           # verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = EVAL_DIR / "baseline_manifest.json"
PROTECTED = ["results", "results_v2", "RESULTS.md", "scenarios"]
TEXT_SUFFIXES = {".csv", ".md", ".json", ".yaml", ".yml", ".txt"}


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def protected_files() -> list[Path]:
    files: list[Path] = []
    for name in PROTECTED:
        path = EVAL_DIR / name
        files += sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    return files


def current() -> dict[str, str]:
    return {p.relative_to(EVAL_DIR).as_posix(): digest(p) for p in protected_files()}


def check() -> list[str]:
    """Every file that changed, disappeared or appeared since the manifest was recorded."""
    expected = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"]
    actual = current()
    return ([f"changed: {k}" for k in sorted(expected) if k in actual and actual[k] != expected[k]]
            + [f"missing: {k}" for k in sorted(expected) if k not in actual]
            + [f"new: {k}" for k in sorted(actual) if k not in expected])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="record the manifest instead of checking it")
    args = parser.parse_args()
    if args.write:
        files = current()
        MANIFEST_PATH.write_text(json.dumps({"protected": PROTECTED, "files": files}, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"wrote {MANIFEST_PATH} ({len(files)} files)")
        return
    problems = check()
    if problems:
        print("Protected benchmark files differ from the manifest:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("All protected benchmark files match the manifest.")


if __name__ == "__main__":
    main()
