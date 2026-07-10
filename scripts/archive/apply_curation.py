"""
apply_curation.py — execute RECIPE_CURATION_PLAN.md dispositions.

Usage:
    python3 apply_curation.py          # dry run: print what would change
    python3 apply_curation.py --apply  # backup library, then apply

Cuts    -> status="retired" (restorable; excluded from planner + swap pools)
Regen   -> stays active, gains regen_pending=True (cleared when the
           regenerated version is saved over the same id)
Keeps   -> untouched

One KV read + one KV write (not 77 round-trips).
"""

import json
import sys
from datetime import datetime

from mealplan.library import KEY_RECIPE_LIBRARY, STATUS_RETIRED
from supabase_kv import kv_get, kv_put

CUTS = [
    # House-rule conflicts
    "gen_gochujang_chicken", "gen_lemon_herb_salmon", "gen_moroccan_turkey_meatballs",
    # Cheffy / fussy / not a family-of-4 weeknight
    "sp_636599", "sp_654072", "sp_658967", "sp_660493", "sp_639606",
    "sp_640266", "sp_663177", "sp_657939", "sp_644135",
    # Serves-2 blog recipes / sides as mains
    "sp_663126", "sp_663149", "sp_716311", "sp_715769", "sp_664835",
    # Data too junky to rebuild
    "sp_637697", "sp_638642", "sp_716408", "sp_645384",
    # Rejected concepts (sibling recipes never-again'd)
    "sp_634965", "sp_664025", "sp_649036", "sp_638369",
    # Marginal / never picked
    "sp_641565", "sp_641893", "sp_642540", "sp_645265", "sp_648462",
    "sp_663033", "sp_663156", "sp_664830", "sp_715573", "sp_716300",
    "sp_716344", "sp_716364", "sp_776505",
]

REGEN = [
    # Wave 1 — in current rotation
    "gen_moroccan_chicken_tagine", "gen_chinese_beef_broccoli",
    "gen_chinese_kung_pao_chicken", "gen_chinese_egg_fried_rice",
    "gen_greek_lemon_chicken_potatoes",
    # Wave 2 — generated concepts worth saving
    "gen_beef_kofta_rice", "gen_black_bean_quesadillas",
    "gen_coconut_chickpea_curry", "gen_greek_shrimp_saganaki",
    "gen_harissa_chicken_chickpeas", "gen_indian_chicken_korma",
    "gen_indian_paneer_tikka", "gen_me_beef_arayes",
    "gen_me_chicken_shawarma_bowls", "gen_me_chickpea_shakshuka",
    "gen_me_tahini_salmon", "gen_moroccan_beef_lentil_soup",
    "gen_moroccan_chickpea_stew", "gen_moroccan_lamb_couscous",
    "gen_moroccan_shrimp_chermoula", "gen_pesto_chicken_skillet",
    "gen_pork_carnitas_tacos", "gen_pork_chops_sheet_pan",
    "gen_pork_tenderloin_slaw", "gen_sausage_peppers_sheet_pan",
    "gen_viet_ginger_pork_bowls", "gen_viet_lemongrass_chicken",
    "gen_viet_shaking_beef",
    # Wave 3 — scraped, good bones
    "sp_1098350", "sp_634710", "sp_641908", "sp_647830", "sp_649030",
    "sp_649040", "sp_645634", "sp_631748", "sp_632874", "sp_648479",
    "sp_715467",
]


def main(apply: bool):
    lib = kv_get(KEY_RECIPE_LIBRARY, {}) or {}
    missing = [r for r in CUTS + REGEN if r not in lib]
    if missing:
        print(f"ABORT — ids not found in library: {missing}")
        sys.exit(1)
    overlap = set(CUTS) & set(REGEN)
    if overlap:
        print(f"ABORT — ids in both lists: {sorted(overlap)}")
        sys.exit(1)

    cut_n = flag_n = 0
    for rid in CUTS:
        if lib[rid].get("status") != STATUS_RETIRED:
            lib[rid]["status"] = STATUS_RETIRED
            cut_n += 1
    for rid in REGEN:
        if not lib[rid].get("regen_pending"):
            lib[rid]["regen_pending"] = True
            flag_n += 1

    active = sum(1 for r in lib.values()
                 if r.get("status") not in ("never_again", "retired"))
    print(f"{'WOULD apply' if not apply else 'Applying'}: "
          f"{cut_n} cut(s) -> retired, {flag_n} regen flag(s). "
          f"Active after: {active} of {len(lib)}")

    if not apply:
        for rid in CUTS:
            print(f"  retire: {rid:30} {lib[rid].get('title','')[:50]}")
        print("Dry run only. Re-run with --apply.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"recipe_library_backup_{stamp}.json"
    with open(backup, "w") as f:
        json.dump(kv_get(KEY_RECIPE_LIBRARY, {}) or {}, f, indent=1)
    print(f"backup written: {backup}")

    kv_put(KEY_RECIPE_LIBRARY, lib)
    print("applied.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
