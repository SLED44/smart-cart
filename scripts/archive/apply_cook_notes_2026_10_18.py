"""
apply_cook_notes_2026_10_18.py — fold saved cook notes into the recipes.

Usage:
    PYTHONPATH=. python3 scripts/archive/apply_cook_notes_2026_10_18.py           # dry run
    PYTHONPATH=. python3 scripts/archive/apply_cook_notes_2026_10_18.py --apply   # backup + write

One-shot. Each edit below is the user's own "Made it with changes" note turned
into ingredients/steps. user_notes are left untouched; every recipe touched (and
the two whose notes were already folded in earlier) gets `notes_applied_at` so
the next review can tell applied notes from pending ones. Changing ingredients
or steps invalidates `step_ingredients` by fingerprint, so the cook pane
re-tags those recipes on next open.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone

from mealplan import library


def _ing(name, amount, unit, aisle, group, text):
    return {"name": name, "amount": amount, "unit": unit, "aisle": aisle,
            "group": group, "original_text": text}


def _steps(*texts):
    return [{"step_number": n, "text": t} for n, t in enumerate(texts, 1)]


def _find(r, name):
    for ing in r["ingredients"]:
        if ing["name"] == name:
            return ing
    raise KeyError(f"{r['id']}: no ingredient {name!r}")


# ---------------------------------------------------------------------------
# Edits — one function per recipe, each mutating the live record in place
# ---------------------------------------------------------------------------

def wings(r):
    # Note (2026-05-31): baking-powder dry toss, air fryer at 400°F, sauce at 25 min.
    r["ingredients"] = [
        _ing("chicken wings", 3.25, "lb", "Meat", "Wings", "3.25 lb chicken wings"),
        _ing("baking powder", 1.0, "tbsp", "Baking", "Dry toss", "1 tbsp baking powder (not soda)"),
        _ing("kosher salt", 1.0, "tsp", "Spices and Seasonings", "Dry toss", "1 tsp salt"),
        _ing("garlic powder", 1.0, "tsp", "Spices and Seasonings", "Dry toss", "1 tsp garlic powder"),
        _ing("black pepper", 0.5, "tsp", "Spices and Seasonings", "Dry toss", "1/2 tsp black pepper"),
        _ing("smoked paprika", 0.5, "tsp", "Spices and Seasonings", "Dry toss", "1/2 tsp smoked paprika"),
        _ing("vegetable oil", 1.5, "tbsp", "Oil, Vinegar, Salad Dressing", "Dry toss", "1-2 tbsp neutral oil"),
        _ing("sriracha", 0.25, "cup", "Condiments", "Sauce", "1/4 cup sriracha (reduced for mild household)"),
        _ing("honey", 4.0, "tsp", "Nut butters, Jams, and Honey", "Sauce", "4 tsp honey"),
        _ing("brown sugar", 4.0, "tbsp", "Baking", "Sauce", "4 tbsp brown sugar"),
        _ing("garlic powder", 2.0, "tbsp", "Spices and Seasonings", "Sauce", "2 tbsp garlic powder"),
        _ing("ground ginger", 2.0, "tbsp", "Spices and Seasonings", "Sauce", "2 tbsp ground ginger"),
        _ing("onion powder", 1.0, "tbsp", "Spices and Seasonings", "Sauce", "1 tbsp onion powder"),
        _ing("chicken broth", 0.25, "cup", "Canned and Jarred", "Sauce", "1/4 cup low-sodium chicken broth"),
        _ing("lime", 0.5, "count", "Produce", "Sauce", "1/2 lime, juiced"),
        _ing("cilantro", 0.5, "cup", "Produce", "For serving", "1/2 cup cilantro, roughly chopped"),
        _ing("frozen french fries", 1, "lb", "Frozen", "For serving", "1 lb frozen french fries"),
        _ing("carrot", 3, "count", "Produce", "For serving", "3 carrots, cut into sticks"),
        _ing("celery", 4, "count", "Produce", "For serving", "4 celery stalks, cut into sticks"),
    ]
    r["instructions"] = _steps(
        "Pat the wings completely dry with paper towels — this is the step that makes them crisp.",
        "In a large bowl, toss the wings with the dry toss (baking powder, salt, 1 tsp garlic powder, pepper, smoked paprika) until evenly coated. Then add the oil and toss again.",
        "Air-fry at 400°F in a single layer (batches if needed), 25 minutes, flipping at the halfway mark.",
        "While they cook, make the sauce: simmer the sriracha, honey, brown sugar, 2 tbsp garlic powder, ginger, onion powder, broth and lime juice in a small saucepan 5-7 minutes until syrupy.",
        "At 25 minutes, brush or toss the wings with half the sauce and air-fry 5-8 minutes more. Watch closely — honey burns fast.",
        "Toss with the remaining sauce and scatter with cilantro.",
        "Air-fry the fries before the wings (they re-crisp in 2 minutes at the end) and serve everything with carrot and celery sticks.",
    )
    r["equipment"] = ["air_fryer", "saucepan"]
    r["prep_minutes"], r["cook_minutes"], r["ready_in_minutes"] = 15, 35, 50


def picadillo(r):
    # Note (2026-09-29): potatoes need ~10 more minutes; add olives.
    peas = r["ingredients"].index(_find(r, "frozen peas"))
    r["ingredients"].insert(peas + 1, _ing(
        "pimento-stuffed green olives", 0.5, "cup", "Canned and Jarred", "Picadillo",
        "1/2 cup pimento-stuffed green olives, sliced"))
    r["instructions"][3]["text"] = ("Cover and simmer 22-25 minutes, stirring occasionally, "
                                    "until the potatoes are fully soft when pierced.")
    r["instructions"][4]["text"] = ("Stir in the peas and olives; cook 3 minutes more. Taste for salt "
                                    "(the olives add some) — it should be cozy and gently spiced, not hot.")
    r["cook_minutes"], r["ready_in_minutes"] = 30, 45


def satay(r):
    # Note (2026-06-16): more soy in the peanut sauce; more veggies in the dish.
    # Snap peas were already added; put a vegetable on the skewers too.
    soy = _find(r, "soy sauce")
    soy["amount"], soy["original_text"] = 3.0, "3 tbsp soy sauce"
    r["ingredients"].insert(r["ingredients"].index(_find(r, "sugar snap peas")), _ing(
        "red bell pepper", 2.0, "count", "Produce", "Vegetables", "2 red bell peppers, cut into 1-inch pieces"))
    r["instructions"][1]["text"] = r["instructions"][1]["text"].replace("2 tbsp soy", "3 tbsp soy")
    r["instructions"][2]["text"] = ("Thread the chicken onto skewers alternating with the bell pepper, "
                                    "or leave as strips and toss the peppers in with them for the air fryer.")
    r["instructions"][3]["text"] = ("Air-fry at 400°F for 12-14 minutes (or grill 4-5 min/side), turning once, "
                                    "until the chicken is charred at the edges and 175°F and the peppers are blistered.")
    r["instructions"][-1]["text"] = ("Serve the satay and peppers over rice with the peanut sauce for dunking, "
                                     "the snap peas, and cucumber alongside. The kids will dip everything.")


def pasta_e_fagioli(r):
    # Note (2026-06-18): used 9 oz pasta; portions looked cut down; say canned tomatoes.
    # The odd portions were servings_original=5 being scaled to a household of 4
    # (×0.8 → 0.8 can tomatoes, 1.6 cans beans). Serves-4 shows whole cans.
    r["servings_original"] = 4
    fixes = {
        "whole peeled tomatoes (28 oz)": ("canned whole peeled tomatoes (28 oz)", None,
                                          "1 (28-oz) can whole peeled tomatoes"),
        "short pasta": (None, 9, "9 oz short pasta (ditalini or elbows)"),
        "Italian seasoning": (None, None, "1/4 tsp Italian seasoning (or dried oregano)"),
        "cannellini beans (15 oz)": (None, None, "2 (15-oz) cans cannellini beans, drained"),
        "baby spinach": (None, None, "4 oz baby spinach"),
        "grated Parmesan": (None, None, "1/2 cup grated Parmesan, for serving"),
        "fresh parsley": (None, None, "1/4 cup chopped fresh parsley, for serving"),
    }
    for old, (name, amount, text) in fixes.items():
        ing = _find(r, old)
        if name:
            ing["name"] = name
        if amount is not None:
            ing["amount"] = amount
        ing["original_text"] = text
    r["instructions"][1]["text"] = ("Add the broth, the canned tomatoes with their juice, pasta, Italian seasoning, "
                                    "and a pinch each of salt and pepper. Bring to a boil, crushing the tomatoes with a spoon.")
    r["instructions"][2]["text"] = r["instructions"][2]["text"].replace(
        "cook until pasta is al dente, 7-9 min",
        "cook until the pasta is al dente and the soup is thick, 8-10 min")


def miso_salmon(r):
    # Note (2026-09-28): slice the bok choy and sauté it, don't just halve it.
    bc = _find(r, "baby bok choy")
    bc["original_text"] = "1 lb baby bok choy, sliced 1 inch thick (stems and leaves kept apart)"
    r["ingredients"].append(_ing("vegetable oil", 1.0, "tbsp", "Pantry", "Vegetables",
                                 "1 tbsp vegetable oil (for the bok choy)"))
    r["instructions"][4]["text"] = (
        "While the salmon cooks, slice the bok choy crosswise 1 inch thick, keeping stems and leaves apart. "
        "Sauté the stems in 1 tbsp oil over medium-high 2-3 minutes, add the leaves and a pinch of salt, "
        "and toss until just wilted, about 1 minute more.")


def lentil_soup(r):
    # Note (2026-10-11): good with sour cream; should have naan on the side.
    r["ingredients"] += [
        _ing("sour cream", 0.5, "cup", "Dairy", "For serving", "1/2 cup sour cream, for topping"),
        _ing("naan", 4.0, "count", "Bakery", "For serving", "4 naan, warmed"),
    ]
    r["instructions"][-1]["text"] = ("Taste for salt. Warm the naan (oven or air fryer, 2-3 minutes) and serve the soup "
                                     "with a spoonful of sour cream on top and naan for dunking.")
    r["diet_tags"] = [t for t in r["diet_tags"] if t != "vegan"]   # sour cream


def greek_turkey_burgers(r):
    # Note (2026-05-31): added asparagus; needs a veggie. Make the side part of the recipe.
    texts = {"feta cheese": "3 oz feta, crumbled", "baby spinach": "2 oz baby spinach, finely chopped",
             "garlic": "2 garlic cloves, grated", "cucumber": "1 cucumber, grated, for tzatziki",
             "tomato": "1 tomato, sliced"}
    for name, text in texts.items():
        _find(r, name)["original_text"] = text
    r["ingredients"] += [
        _ing("asparagus", 1.0, "lb", "Produce", "Asparagus", "1 lb asparagus, woody ends snapped off"),
        _ing("olive oil", 1.0, "tbsp", "Oil, Vinegar, Salad Dressing", "Asparagus", "1 tbsp olive oil"),
    ]
    r["instructions"] = _steps(
        "Mix the turkey with feta, chopped spinach, grated garlic, oregano, and salt. Form 4 patties.",
        "Air-fry at 380°F for 12-14 minutes, flipping once, until cooked through (165°F).",
        "While they rest, toss the asparagus with olive oil and a pinch of salt and air-fry at 400°F for 6-8 minutes until tender and lightly charred. Finish with a squeeze of the lemon.",
        "Stir the yogurt with grated cucumber, a squeeze of lemon, and salt for tzatziki.",
        "Serve the burgers on buns with tomato, romaine, and a generous spoon of tzatziki, with the asparagus alongside.",
    )
    r["ready_in_minutes"] = 35


def banh_mi(r):
    # Note (2026-10-18): wants a veggie on the side — the pickles go on the sandwich.
    r["ingredients"] += [
        _ing("frozen edamame in pods", 12.0, "oz", "Frozen", "Side", "12 oz frozen edamame in pods"),
        _ing("flaky salt", 0.5, "tsp", "Spices and Seasonings", "Side", "1/2 tsp flaky or kosher salt"),
    ]
    r["instructions"].insert(5, {"step_number": 0, "text": (
        "Side: steam or microwave the edamame per the bag (about 5 minutes) and toss with salt "
        "and a squeeze of lime.")})
    for n, s in enumerate(r["instructions"], 1):
        s["step_number"] = n


EDITS = {
    "sp_633651": wings,
    "gen_picadillo": picadillo,
    "gen_chicken_satay": satay,
    "nyt_pasta_e_fagioli": pasta_e_fagioli,
    "nyt_miso_salmon_bowls": miso_salmon,
    "gen_sc_lentil_veg_soup": lentil_soup,
    "gen_greek_turkey_burgers": greek_turkey_burgers,
    "sp_645634": banh_mi,
}
# Notes already reflected in the recipe text (bolder stir-fry sauce; avocado on the skillet).
ALREADY_APPLIED = ("sp_638714", "gen_turkey_taco_skillet")


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
        edit(r)
        for ing in r["ingredients"]:          # validator wants "" not null
            if ing.get("group") is None:
                ing["group"] = ""
        r["notes_applied_at"] = now
        errs = paste._validate_recipe(r)
        print(("✗ " if errs else "✓ ") + f"{rid} — {r['title']}: "
              f"{len(r['ingredients'])} ingredients, {len(r['instructions'])} steps")
        for e in errs:
            print("    " + e)
        failed |= bool(errs)
    for rid in ALREADY_APPLIED:
        lib[rid]["notes_applied_at"] = now

    if failed:
        sys.exit("validation failed — nothing written")
    if not apply:
        print("\nDry run. Re-run with --apply to write.")
        return
    backup = f"lib_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(backup, "w") as f:
        json.dump(library.get_all(), f, indent=1)
    library._put_all(lib)
    print(f"\nWrote {len(EDITS)} recipes (+{len(ALREADY_APPLIED)} stamped). Backup: {backup}")


if __name__ == "__main__":
    main()
