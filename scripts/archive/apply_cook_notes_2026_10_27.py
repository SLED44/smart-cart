"""
apply_cook_notes_2026_10_27.py — fold the week-12 cook note into its recipe,
and carry it to the other Indian curries.

Usage:
    PYTHONPATH=. python3 scripts/archive/apply_cook_notes_2026_10_27.py           # dry run
    PYTHONPATH=. python3 scripts/archive/apply_cook_notes_2026_10_27.py --apply   # backup + write

One-shot. The only note from the week-12 plan was butter chicken's "include
naan in the ingredients for on the side". Tikka masala and the chickpea curry
already list naan (and the lentil soup got it on 2026-10-18); the dahl, paneer
tikka and korma were the active Indian curries still without it. Naan is
appended, not inserted, because the cook pane keys ingredient checkmarks by
index. Only butter chicken gets `notes_applied_at` — the others carry no note.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone

from mealplan import library


def _naan():
    return {"name": "naan", "amount": 4.0, "unit": "count", "aisle": "Bakery",
            "group": "For serving", "original_text": "4 store-bought naan, warmed"}


def butter_chicken(r):
    # Note (2026-10-28 UTC): "include naan in the ingredients for on the side next time."
    r["ingredients"].append(_naan())
    r["instructions"][-1]["text"] = ("Warm the naan (oven or air fryer, 2-3 minutes). Serve the butter chicken "
                                     "over rice, topped with cilantro, with naan on the side for the sauce.")


def dahl(r):
    r["ingredients"].append(_naan())
    # Spinach was step 7, after the serving step; fold it in before serving.
    spinach, serve = r["instructions"][6], r["instructions"][5]
    assert "spinach" in spinach["text"] and serve["text"].startswith("Serve"), "dahl steps moved"
    r["instructions"][5:7] = [
        {"step_number": 6, "text": spinach["text"]},
        {"step_number": 7, "text": "Serve over rice with cilantro, a lemon wedge (the squeeze matters), "
                                   "and warm naan for scooping."},
    ]


def paneer(r):
    r["ingredients"].append(_naan())
    r["instructions"][-1]["text"] = ("Serve over basmati rice with warm naan on the side. "
                                     "(Adults: chili crisp at the table if you want heat.)")


def korma(r):
    r["ingredients"].append(_naan())
    serve = r["instructions"][7]
    assert serve["text"].startswith("Serve over basmati"), "korma steps moved"
    serve["text"] = "Serve over basmati rice with cilantro and warm naan. Mild, creamy, and kid-safe by design."


EDITS = {
    "gen_sc_butter_chicken": butter_chicken,
    "sp_647830": dahl,
    "gen_indian_paneer_tikka": paneer,
    "gen_indian_chicken_korma": korma,
}
NOTED = ("gen_sc_butter_chicken",)


def main():
    apply = "--apply" in sys.argv
    spec = importlib.util.spec_from_file_location("paste", "screens/mealplan_paste_recipe.py")
    paste = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paste)

    lib = library.get_all()
    now = datetime.now(timezone.utc).isoformat()
    failed = False
    for rid, edit in EDITS.items():
        r = lib[rid]
        if any(i["name"] == "naan" for i in r["ingredients"]):
            sys.exit(f"{rid} already lists naan — already applied?")
        edit(r)
        for ing in r["ingredients"]:          # validator wants "" not null
            if ing.get("group") is None:
                ing["group"] = ""
        if rid in NOTED:
            r["notes_applied_at"] = now
        errs = paste._validate_recipe(r)
        print(("✗ " if errs else "✓ ") + f"{rid} — {r['title']}: "
              f"{len(r['ingredients'])} ingredients, {len(r['instructions'])} steps")
        for e in errs:
            print("    " + e)
        failed |= bool(errs)

    if failed:
        sys.exit("validation failed — nothing written")
    if not apply:
        print("\nDry run. Re-run with --apply to write.")
        return
    backup = f"lib_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(backup, "w") as f:
        json.dump(library.get_all(), f, indent=1)
    library._put_all(lib)
    print(f"\nWrote {len(EDITS)} recipes. Backup: {backup}")


if __name__ == "__main__":
    main()
