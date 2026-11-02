#!/usr/bin/env python3
"""CLI: build a character's LoRA training set, then train the LoRA (W6).

Usage::

    python tools/train_lora.py dataset <project_dir> <CHARACTER> [--force] [--set KEY=VALUE ...]
    python tools/train_lora.py train   <project_dir> <CHARACTER> [--force] [--set KEY=VALUE ...]

* ``dataset`` has Wan2GP draw the character from its reference in varied
  shots (``lazycomics.generate_training_set``, ~10 min on AC) and writes
  ``assets/characters/<CHARACTER>/lora_dataset_sheet.png``. Hand-pick from
  the sheet: move rejects into ``lora_dataset/rejected/``. Re-running
  only fills in missing images; ``--force`` starts the set over.
* ``train`` runs AI Toolkit on what's left (``lazycomics.train_character_lora``)
  and attaches the LoRA to the character, with its ID as the trigger word.
  An existing LoRA is reattached, not retrained, unless ``--force``.
* ``--set`` overrides any config value, as in ``golden_run.py``, e.g.
  ``--set ai_toolkit.steps=300`` for a short trial run.

Works on any project. For a golden A/B, train in the golden run's own
project, then hand the LoRA to the run (every golden run re-registers its
characters)::

    python tools/golden_run.py --out w6_gerald --no-generate     # creates the project
    python tools/train_lora.py dataset tests/e2e/outputs/golden/w6_gerald/project GERALD
    python tools/train_lora.py train   tests/e2e/outputs/golden/w6_gerald/project GERALD
    python tools/golden_run.py --out w6_gerald --force --baseline w4_prompts \\
        --lora GERALD=<path printed by train> --set enricher.multi_char_strategy=single
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import review  # noqa: E402
from lazycomics.config import load_config  # noqa: E402

from golden_run import _override  # noqa: E402  (sibling script in tools/)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=["dataset", "train"])
    ap.add_argument("project_dir")
    ap.add_argument("character")
    ap.add_argument("--force", action="store_true",
                    help="dataset: start the set over; train: retrain from scratch")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. ai_toolkit.steps=300 (repeatable)")
    args = ap.parse_args(argv)

    config = copy.deepcopy(load_config(REPO / "lazycomics_config.yaml"))
    for item in args.set:
        _override(config, item)
    project = lc.load_project(args.project_dir)

    if args.step == "dataset":
        images = lc.generate_training_set(project, args.character, force=args.force, config=config)
        # Beside the dataset, not in it: anything in it gets trained on.
        char_dir = project.assets_dir / "characters" / args.character
        sheet = review.dataset_sheet(images, char_dir / "lora_dataset_sheet.png", title=(
            f"{args.character}: {len(images)} training images. "
            f"Move rejects into {char_dir / 'lora_dataset' / 'rejected'}"))
        print(f"\n{len(images)} image(s) in the training set\nsheet: {sheet}")
    else:
        path = lc.train_character_lora(project, args.character, force=args.force, config=config)
        print(f"\nLoRA: {path}\nattached to {args.character} (trigger word {args.character!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
