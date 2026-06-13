"""
apply_wave.py — validate + apply a recipe regeneration wave.

Usage:
    python3 apply_wave.py wave1_recipes.py           # dry run
    python3 apply_wave.py wave1_recipes.py --apply   # backup + save

Each payload module exposes WAVE: list[recipe]. Every recipe is validated
against the paste-recipe validator (the single quality gate), then saved
via library.save — same id overwrites in place and preserves cooked
history / status / user notes.
"""

import importlib.util
import json
import sys
from datetime import datetime

from mealplan import library
from supabase_kv import kv_get


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)

    wave = _load_module(args[0], "wave_payload").WAVE
    # Bypass screens/__init__ (pulls the whole app); we only need the validator.
    paste = _load_module("screens/mealplan_paste_recipe.py", "paste_recipe")

    lib = library.get_all()
    failures = 0
    for r in wave:
        errs = paste._validate_recipe(r)
        rid = r.get("id", "(no id)")
        mode = "overwrite" if rid in lib else "NEW"
        if errs:
            failures += 1
            print(f"✗ {rid} [{mode}]")
            for e in errs:
                print(f"    {e}")
        else:
            n_groups = len({i.get('group') for i in r['ingredients'] if i.get('group')})
            print(f"✓ {rid} [{mode}] — {r['title']}: "
                  f"{len(r['ingredients'])} ingredients / {n_groups} groups, "
                  f"{len(r['instructions'])} steps")

    if failures:
        print(f"\n{failures} recipe(s) failed validation — nothing applied.")
        sys.exit(1)
    if not apply:
        print("\nDry run OK. Re-run with --apply.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"recipe_library_backup_{stamp}.json"
    with open(backup, "w") as f:
        json.dump(kv_get(library.KEY_RECIPE_LIBRARY, {}) or {}, f, indent=1)
    print(f"\nbackup written: {backup}")

    for r in wave:
        rid = library.save(dict(r))
        print(f"saved: {rid}")
    print(f"applied {len(wave)} recipe(s).")


if __name__ == "__main__":
    main()
