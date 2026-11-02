"""
Freeze the pre-registration before the held-out run (Phase 9 → 10).

Fills in what the draft left open (the split's trip lists, the tuned beam widths, the hashes
of every ground-truth file), refuses unless every ground truth verifies, sets ``status: frozen``
and records ``frozen_sha256`` = sha256 of the file with that value blanked
(``experiments.prereg_digest``). After this, ``experiments.py --split heldout`` checks the hash
before it runs, and any change must be listed under ``deviations``.

    backend\\.venv\\Scripts\\python.exe eval\\freeze_preregistration.py --w-star 10 --w-prime 20
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval.ground_truth as gt  # noqa: E402
from eval import scenario_set_v3 as v3  # noqa: E402
from eval.experiments import PREREG, prereg_digest  # noqa: E402


def _replace(text: str, key: str, value: str) -> str:
    """Replace the value of a top-level or indented ``key:`` line (first match), keeping any comment."""
    pattern = re.compile(rf"^(\s*{re.escape(key)}:)\s*[^#\n]*?(\s*(#.*)?)$", re.MULTILINE)
    new, n = pattern.subn(lambda m: f"{m.group(1)} {value}{m.group(2) or ''}", text, count=1)
    if n != 1:
        raise SystemExit(f"key {key!r} not found in the pre-registration")
    return new


def freeze(w_star: int, w_prime: int) -> str:
    text = PREREG.read_text(encoding="utf-8")
    if yaml.safe_load(text)["status"] == "frozen":
        raise SystemExit("already frozen")
    hashes = {}
    for split in v3.SPLITS:
        for catalogue in v3.CATALOGUES:
            gt.load_verified(split, catalogue)                      # raises if anything changed
            hashes[f"{split}_{catalogue}"] = gt.paths(split, catalogue)[1].read_text(encoding="utf-8").strip()
    text = _replace(text, "dev_trips", "[" + ", ".join(v3.load_trips("dev")) + "]")
    text = _replace(text, "heldout_trips", "[" + ", ".join(v3.load_trips("heldout")) + "]")
    text = text.rstrip("\n") + "\n\nfrozen_values:   # filled in by eval/freeze_preregistration.py\n" \
        f"  beam_width_w_star: {w_star}\n  e2_prime_width: {w_prime}\n  ground_truth_sha256:\n" + \
        "".join(f"    {k}: {v}\n" for k, v in hashes.items())
    text = _replace(text, "status", "frozen")
    text = _replace(text, "frozen_at", date.today().isoformat())
    text = _replace(text, "frozen_sha256", prereg_digest(text))
    PREREG.write_text(text, encoding="utf-8", newline="\n")
    return prereg_digest(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--w-star", type=int, required=True)
    parser.add_argument("--w-prime", type=int, required=True)
    args = parser.parse_args()
    print(f"frozen; sha256 {freeze(args.w_star, args.w_prime)}")


if __name__ == "__main__":
    main()
