"""
mealplan/_tests.py
------------------
Synthetic-library tests for rules + planner. No Supabase, no LLM, no network.

Run with:  python3 -m mealplan._tests
"""

import sys
import traceback

from mealplan.rules import (
    Evaluation,
    bump_state_after_confirm,
    default_rules,
    evaluate_candidate,
    relaxation_label,
    validate_rules,
)
from mealplan.planner import (
    LineupResult,
    NoCandidatesError,
    generate_lineup,
    lineup_meta,
    regenerate_lineup,
)


# ---------------------------------------------------------------------------
# Synthetic recipe factory + library
# ---------------------------------------------------------------------------

def R(
    id: str,
    title: str = "",
    cuisines=None,
    proteins=None,
    carbs=None,
    status: str = "active",
    last_cooked_at: str | None = None,
    equipment=None,
) -> dict:
    return {
        "id":             id,
        "title":          title or id.replace("_", " ").title(),
        "cuisines":       cuisines or [],
        "proteins":       proteins or [],
        "carbs":          carbs or [],
        "status":         status,
        "last_cooked_at": last_cooked_at,
        "equipment":      equipment or [],
    }


def synthetic_library() -> list[dict]:
    """A small but diverse library covering most rules engine paths."""
    return [
        # American (beef × 2 — testing absolute ceiling)
        R("burger_classic",       cuisines=["american"], proteins=["beef"],    carbs=["bread"]),
        R("burger_blue_cheese",   cuisines=["american"], proteins=["beef"],    carbs=["bread"]),
        R("meatloaf",             cuisines=["american"], proteins=["beef"],    carbs=["potato"]),
        # Italian
        R("chicken_parmesan",     cuisines=["italian"],  proteins=["chicken"], carbs=["pasta"]),
        R("salmon_lemon_pasta",   cuisines=["italian"],  proteins=["fish"],    carbs=["pasta"]),
        # Mexican
        R("chicken_tacos",        cuisines=["mexican"],  proteins=["chicken"], carbs=["bread"]),
        R("pork_carnitas",        cuisines=["mexican"],  proteins=["pork"],    carbs=["rice"]),
        # Japanese
        R("chicken_katsu",        cuisines=["japanese"], proteins=["chicken"], carbs=["rice"]),
        R("miso_glazed_salmon",   cuisines=["japanese"], proteins=["fish"],    carbs=["pasta"]),
        # Greek
        R("lamb_meatballs",       cuisines=["greek"],    proteins=["lamb"],    carbs=["bread"]),
        # Moroccan
        R("moroccan_pork",        cuisines=["moroccan"], proteins=["pork"],    carbs=["grain"]),
        # Vietnamese (vegetarian)
        R("tofu_banh_mi",         cuisines=["vietnamese"], proteins=["plant"], carbs=["pasta"]),
        # Shrimp
        R("shrimp_scampi",        cuisines=["italian"],  proteins=["shrimp"],  carbs=["pasta"]),
        # Spicy (should be filtered for mild households)
        R("spicy_thai_basil",     cuisines=["thai"],     proteins=["chicken"], carbs=["rice"],
          title="Spicy Thai Basil"),
        # Never again
        R("honey_garlic_salmon",  cuisines=["american"], proteins=["fish"],    carbs=["rice"],
          status="never_again"),
        # Salad
        R("greek_salad_bowl",     cuisines=["greek"],    proteins=["chicken"], carbs=["salad"]),
    ]


# ---------------------------------------------------------------------------
# Test framework — tiny so the test file is self-contained
# ---------------------------------------------------------------------------

class _T:
    """Mini test harness — green dot per pass, traceback per fail, summary."""
    def __init__(self):
        self.passes = 0
        self.failures: list[tuple[str, str]] = []

    def case(self, name: str):
        def deco(fn):
            try:
                fn()
                self.passes += 1
                print(f"  ✓ {name}")
            except Exception as e:
                self.failures.append((name, traceback.format_exc()))
                print(f"  ✗ {name}: {e}")
            return fn
        return deco

    def summary(self) -> int:
        total = self.passes + len(self.failures)
        print()
        print(f"--- {self.passes}/{total} passed ---")
        if self.failures:
            print()
            for name, tb in self.failures:
                print(f"FAIL: {name}")
                print(tb)
            return 1
        return 0


# ---------------------------------------------------------------------------
# Rules engine tests
# ---------------------------------------------------------------------------

def rules_tests(t: _T):
    print("\n== rules engine ==")

    @t.case("validate_rules: defaults are valid")
    def _():
        assert validate_rules(default_rules()) == []

    @t.case("validate_rules: catches missing fields")
    def _():
        bad = default_rules()
        del bad["household"]["spice"]
        errs = validate_rules(bad)
        assert any("spice" in e for e in errs), errs

    @t.case("hard: exclusion list rejects")
    def _():
        rules = default_rules()
        rules["exclusions"] = ["burger_classic"]
        ev = evaluate_candidate(R("burger_classic", proteins=["beef"]), rules, [], [])
        assert not ev.eligible
        assert "exclusion" in ev.rejection_reason

    @t.case("hard: never_again status rejects")
    def _():
        ev = evaluate_candidate(R("x", status="never_again"), default_rules(), [], [])
        assert not ev.eligible

    @t.case("hard: pair-exclusion fires only when partner present")
    def _():
        rules = default_rules()
        rules["pair_exclusions"] = [["a", "b"]]
        a = R("a", proteins=["chicken"])
        b = R("b", proteins=["beef"])
        # b alone: a should be rejected when b is in lineup
        ev = evaluate_candidate(a, rules, [b], [])
        assert not ev.eligible
        # a alone (no b): should be eligible
        ev2 = evaluate_candidate(a, rules, [], [])
        assert ev2.eligible

    @t.case("hard: absolute protein ceiling never relaxed")
    def _():
        rules = default_rules()
        # Default: beef ceiling = 2. Two beef already in lineup, third rejected.
        lineup = [R("a", proteins=["beef"]), R("b", proteins=["beef"])]
        for level in range(5):
            ev = evaluate_candidate(R("c", proteins=["beef"]), rules, lineup, [], level)
            assert not ev.eligible, f"L{level} should still reject"

    @t.case("hard: vegetarian cap never relaxed")
    def _():
        # Make the test independent of the default ceiling value — pin
        # ceiling=1 explicitly so this case verifies the mechanism, not
        # the specific dietitian-default value.
        rules = default_rules()
        rules["protein_limits"]["plant"]["absolute_ceiling"] = 1
        lineup = [R("tofu", proteins=["plant"])]
        for level in range(5):
            ev = evaluate_candidate(R("lentil", proteins=["plant"]), rules, lineup, [], level)
            assert not ev.eligible, f"L{level} should reject second plant"

    @t.case("hard: shrimp cadence rejects when too recent")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 5
        for entry in rules["protein_cadences"]:
            if entry["protein"] == "shrimp":
                entry["last_used_week"] = 3  # 2 weeks ago, cadence is 4
        for level in range(5):
            ev = evaluate_candidate(R("scampi", proteins=["shrimp"]), rules, [], [], level)
            assert not ev.eligible

    @t.case("hard: shrimp cadence passes when far enough back")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 10
        for entry in rules["protein_cadences"]:
            if entry["protein"] == "shrimp":
                entry["last_used_week"] = 3
        ev = evaluate_candidate(R("scampi", proteins=["shrimp"]), rules, [], [], 0)
        assert ev.eligible

    @t.case("hard: spicy title rejected when household=mild")
    def _():
        ev = evaluate_candidate(
            R("spicy_thai", title="Spicy Thai Basil", proteins=["chicken"]),
            default_rules(), [], [], 0)
        assert not ev.eligible
        # Setting spice=hot lets it through
        rules = default_rules()
        rules["household"]["spice"] = "hot"
        ev2 = evaluate_candidate(
            R("spicy_thai", title="Spicy Thai Basil", proteins=["chicken"]),
            rules, [], [], 0)
        assert ev2.eligible

    @t.case("soft: variety penalty -20 cuisine, -15 protein, -15 carb")
    def _():
        rules = default_rules()
        lineup = [R("a", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"])]
        # New italian/chicken/pasta candidate should take -50 vs base 100
        cand = R("b", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"])
        ev = evaluate_candidate(cand, rules, lineup, [], 0)
        assert ev.eligible
        # No cap penalty since chicken has no max_per_week; no favorites bonus.
        # Recent-history off (empty history). Just variety: 100 - 20 - 15 - 15 = 50
        assert ev.score == 50, ev.score

    @t.case("soft: must-include cuisine adds +20")
    def _():
        rules = default_rules()
        # American is must-include; chicken has no caps.
        cand = R("burger", cuisines=["american"], proteins=["chicken"], carbs=["bread"])
        ev = evaluate_candidate(cand, rules, [], [], 0)
        assert ev.score == 120, ev.score

    @t.case("soft: favorite due cadence_weeks[0] adds +30")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 5
        rules["favorites"] = [{
            "recipe_id":     "burger_classic",
            "cadence_weeks": [4, 6],
            "last_used_week": 1,   # 4 weeks ago → due
        }]
        cand = R("burger_classic", cuisines=["american"], proteins=["beef"], carbs=["bread"])
        ev = evaluate_candidate(cand, rules, [], [], 0)
        # 100 base + 30 favorite due + 20 must-include = 150
        assert ev.score == 150, ev.score

    @t.case("soft: favorite force window (cadence_weeks[1]) totals +50")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 7
        rules["favorites"] = [{
            "recipe_id": "burger_classic", "cadence_weeks": [4, 6], "last_used_week": 1,
        }]
        cand = R("burger_classic", cuisines=["american"], proteins=["beef"], carbs=["bread"])
        ev = evaluate_candidate(cand, rules, [], [], 0)
        # 100 + 50 favorite (force) + 20 must-include = 170
        assert ev.score == 170, ev.score

    @t.case("soft: soft cap penalty -30 when beef max_per_week=1 exceeded")
    def _():
        rules = default_rules()
        lineup = [R("a", proteins=["beef"])]
        # Default beef.max_per_week=1, ceiling=2. Second beef is soft-cap breach.
        cand = R("b", proteins=["beef"], cuisines=["american"])
        ev = evaluate_candidate(cand, rules, lineup, [], 0)
        assert ev.eligible, "still eligible since under ceiling"
        # 100 base - 15 protein-variety + 20 must-include - 30 soft-cap = 75
        assert ev.score == 75, ev.score

    @t.case("soft: appliance bonus +10 when recipe matches default_appliance")
    def _():
        rules = default_rules()  # default_appliance = "air_fryer"
        cand_air = R("a", cuisines=["italian"], proteins=["chicken"],
                     carbs=["pasta"], equipment=["air_fryer", "sheet_pan"])
        cand_stove = R("b", cuisines=["italian"], proteins=["chicken"],
                       carbs=["pasta"], equipment=["stovetop"])
        ev_air = evaluate_candidate(cand_air, rules, [], [], 0)
        ev_stove = evaluate_candidate(cand_stove, rules, [], [], 0)
        # Same base (100, no must-include since italian's not must-include),
        # air-fryer recipe gets +10.
        assert ev_air.score - ev_stove.score == 10, (ev_air.score, ev_stove.score)

    @t.case("soft: recently cooked -40 from history")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 5
        history = [
            {"week_number": 4, "meals": [{"recipe_id": "burger_classic"}]},
        ]
        cand = R("burger_classic", cuisines=["american"], proteins=["beef"], carbs=["bread"])
        ev = evaluate_candidate(cand, rules, [], history, 0)
        # 100 + 20 must-include - 40 recent = 80
        assert ev.score == 80, ev.score

    @t.case("L1: variety penalties lifted")
    def _():
        rules = default_rules()
        lineup = [R("a", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"])]
        cand = R("b", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"])
        ev0 = evaluate_candidate(cand, rules, lineup, [], 0)
        ev1 = evaluate_candidate(cand, rules, lineup, [], 1)
        assert ev1.score - ev0.score == 50, (ev0.score, ev1.score)
        assert any("variety" in r for r in ev1.relaxations_applied)

    @t.case("L2: favorites cadence bonus lifted")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 7
        rules["favorites"] = [{
            "recipe_id": "burger_classic", "cadence_weeks": [4, 6], "last_used_week": 1,
        }]
        cand = R("burger_classic", cuisines=["american"], proteins=["beef"], carbs=["bread"])
        ev0 = evaluate_candidate(cand, rules, [], [], 0)
        ev2 = evaluate_candidate(cand, rules, [], [], 2)
        assert ev0.score - ev2.score == 50, (ev0.score, ev2.score)

    @t.case("L3: must-include cuisine bonus lifted")
    def _():
        rules = default_rules()
        cand = R("burger", cuisines=["american"], proteins=["chicken"], carbs=["bread"])
        ev0 = evaluate_candidate(cand, rules, [], [], 0)
        ev3 = evaluate_candidate(cand, rules, [], [], 3)
        assert ev0.score - ev3.score == 20, (ev0.score, ev3.score)

    @t.case("L4: soft caps no longer penalised — compared to L3 (only diff)")
    def _():
        # Compare L3 vs L4 to isolate the soft-cap relaxation. Comparing L0 vs L4
        # would conflate variety + must-include + soft-cap (all lifted by L4).
        rules = default_rules()
        lineup = [R("a", proteins=["beef"])]
        cand = R("b", proteins=["beef"], cuisines=["american"])
        ev3 = evaluate_candidate(cand, rules, lineup, [], 3)
        ev4 = evaluate_candidate(cand, rules, lineup, [], 4)
        # L3: variety+must-include both lifted, soft cap still -30 → base 100 - 30 = 70
        # L4: soft cap also lifted → base 100
        assert ev4.score - ev3.score == 30, (ev3.score, ev4.score)
        # Third beef still rejected (ceiling=2) even at L4
        lineup2 = [R("a", proteins=["beef"]), R("b", proteins=["beef"])]
        ev5 = evaluate_candidate(R("c", proteins=["beef"]), rules, lineup2, [], 4)
        assert not ev5.eligible

    @t.case("bump_state: increments week, stamps favorites + shrimp")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 6
        rules["favorites"] = [{
            "recipe_id": "burger_classic", "cadence_weeks": [4, 6], "last_used_week": 2,
        }]
        confirmed = [
            R("burger_classic", proteins=["beef"]),
            R("shrimp_scampi",  proteins=["shrimp"]),
        ]
        new_rules = bump_state_after_confirm(rules, confirmed)
        assert new_rules["state"]["current_week"] == 7
        assert new_rules["state"]["shrimp_counter"] == 0
        # Favorites stamp
        fav = next(f for f in new_rules["favorites"] if f["recipe_id"] == "burger_classic")
        assert fav["last_used_week"] == 6
        # Shrimp cadence stamp
        sh = next(e for e in new_rules["protein_cadences"] if e["protein"] == "shrimp")
        assert sh["last_used_week"] == 6
        # last_plan_confirmed_at set
        assert new_rules["state"]["last_plan_confirmed_at"]

    @t.case("bump_state: shrimp_counter increments when no shrimp")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 6
        rules["state"]["shrimp_counter"] = 2
        new_rules = bump_state_after_confirm(rules, [R("burger", proteins=["beef"])])
        assert new_rules["state"]["shrimp_counter"] == 3


# ---------------------------------------------------------------------------
# Planner tests
# ---------------------------------------------------------------------------

def planner_tests(t: _T):
    print("\n== planner ==")

    @t.case("generate_lineup: produces N unique recipes from a diverse library")
    def _():
        lib = synthetic_library()
        result = generate_lineup(5, default_rules(), lib)
        assert len(result) == 5
        ids = [s.recipe["id"] for s in result.slots]
        assert len(set(ids)) == 5, f"duplicate slot: {ids}"
        # never_again skipped
        assert "honey_garlic_salmon" not in ids
        # spicy_thai_basil should be filtered (household=mild)
        assert "spicy_thai_basil" not in ids

    @t.case("generate_lineup: respects must-include cuisines preference")
    def _():
        lib = synthetic_library()
        result = generate_lineup(5, default_rules(), lib)
        cuisines = set()
        for s in result.slots:
            cuisines.update(c.lower() for c in s.recipe.get("cuisines", []))
        # American, italian, or mexican must appear given the library + bonus.
        assert {"american", "italian", "mexican"} & cuisines

    @t.case("generate_lineup: hits absolute beef ceiling on all-beef library")
    def _():
        # Library is 4 beef recipes only; ceiling is 2. Asking for 3 must fail.
        lib = [
            R("a", proteins=["beef"]), R("b", proteins=["beef"]),
            R("c", proteins=["beef"]), R("d", proteins=["beef"]),
        ]
        try:
            generate_lineup(3, default_rules(), lib)
            raise AssertionError("expected NoCandidatesError on slot 3")
        except NoCandidatesError as e:
            assert e.slot_index == 2
            assert len(e.partial) == 2

    @t.case("identical-library fills all slots at L0 (variety penalty is soft)")
    def _():
        # PRD §8.2 design choice: variety penalties are scoring-only, not
        # eligibility-affecting. With 3 identical chicken-pasta-italian
        # recipes, slot 1+ gets -50 score but stays eligible → all 3 fill
        # at L0. Relaxation NEVER escalates under this PRD interpretation
        # except when hard rules zero out eligibility (in which case
        # relaxation can't help either — see the all-beef test).
        lib = [
            R("a", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"]),
            R("b", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"]),
            R("c", cuisines=["italian"], proteins=["chicken"], carbs=["pasta"]),
        ]
        result = generate_lineup(3, default_rules(), lib)
        assert len(result) == 3
        assert max(result.relaxations_used) == 0, result.relaxations_used
        # First slot scores highest (no variety penalty, must-include +20).
        # Subsequent slots eat the full -50 variety penalty.
        assert result.slots[0].score > result.slots[1].score

    @t.case("generate_lineup: forces favorites in once due")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 10
        rules["favorites"] = [{
            "recipe_id": "lamb_meatballs", "cadence_weeks": [4, 6], "last_used_week": 1,
        }]
        result = generate_lineup(3, rules, synthetic_library())
        ids = {s.recipe["id"] for s in result.slots}
        assert "lamb_meatballs" in ids

    @t.case("regenerate_lineup: avoids prior set when alternates exist")
    def _():
        lib = synthetic_library()
        first = generate_lineup(3, default_rules(), lib)
        second = regenerate_lineup(3, default_rules(), lib,
                                   history=[], prior_lineup=first.recipes)
        first_ids = {s.recipe["id"] for s in first.slots}
        second_ids = {s.recipe["id"] for s in second.slots}
        # Library has > 3 eligible recipes — at least one slot should differ.
        assert first_ids != second_ids, (first_ids, second_ids)

    @t.case("recent-history penalty steers away from last week's meals")
    def _():
        rules = default_rules()
        rules["state"]["current_week"] = 5
        lib = synthetic_library()
        history = [
            {"week_number": 4, "meals": [
                {"recipe_id": "burger_classic"},
                {"recipe_id": "chicken_parmesan"},
            ]},
        ]
        result = generate_lineup(3, rules, lib, history=history)
        ids = {s.recipe["id"] for s in result.slots}
        # Library has chicken_tacos / chicken_katsu / katsu etc. — burger_classic
        # and chicken_parmesan should be deprioritised. Not absolute, but
        # extremely unlikely to be top scorers.
        assert "burger_classic" not in ids or "chicken_parmesan" not in ids, ids

    @t.case("lineup_meta surfaces protein/cuisine/relaxation summary")
    def _():
        lib = synthetic_library()
        result = generate_lineup(4, default_rules(), lib)
        meta = lineup_meta(result, default_rules())
        assert sum(meta["protein_counts"].values()) >= 4
        assert sum(meta["cuisine_counts"].values()) >= 4
        assert "relaxation_summary" in meta

    @t.case("relaxation_label maps integer levels to readable strings")
    def _():
        assert "no relaxation" in relaxation_label(0)
        assert "variety" in relaxation_label(1)
        assert "favorites" in relaxation_label(2)
        assert "cuisine" in relaxation_label(3)
        assert "soft caps" in relaxation_label(4)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Ingredient rendering / scaling tests (screens/_recipe_view.py)
# ---------------------------------------------------------------------------

def recipe_view_tests(t: _T):
    from screens._recipe_view import format_ingredient_line

    can_tomato = {
        "amount": 1, "unit": "count", "name": "whole peeled tomatoes",
        "original_text": "1 (28-oz) can",
    }
    cans_beans = {
        "amount": 2, "unit": "count", "name": "cannellini beans",
        "original_text": "2 (15-oz) cans, drained",
    }
    cloves = {
        "amount": 4, "unit": "count", "name": "garlic",
        "original_text": "4 garlic cloves, thinly sliced",
    }

    @t.case("scaling down a can never shows a fractional can")
    def _():
        # 0.8x is the live household case (size 4 / serves 5).
        line = format_ingredient_line(can_tomato, 0.8)
        assert "0.8" not in line and "count" not in line, line
        # original phrasing (with size + 'can') survives, name re-attached
        assert "can" in line and "tomatoes" in line, line

    @t.case("scaling down rounds whole-item count back to original, not a fraction")
    def _():
        line = format_ingredient_line(cans_beans, 0.8)
        assert "1.6" not in line, line
        assert "2 (15-oz) cans" in line and "cannellini beans" in line, line

    @t.case("scaling up multiplies discrete units to a whole number")
    def _():
        assert format_ingredient_line(cans_beans, 2.0).strip() == "**4** cannellini beans"
        assert format_ingredient_line(can_tomato, 3.0).strip() == "**3** whole peeled tomatoes"

    @t.case("'count' placeholder unit is never shown to the cook")
    def _():
        # 4 cloves * 0.8 = 3.2 -> 3 (count changed, so no original fallback)
        line = format_ingredient_line(cloves, 0.8)
        assert line.strip() == "**3** garlic", line

    @t.case("non-discrete units still scale with kitchen fractions")
    def _():
        broth = {"amount": 4, "unit": "cup", "name": "chicken broth",
                 "original_text": "4 cup chicken broth"}
        assert format_ingredient_line(broth, 0.5).strip() == "**2** cup chicken broth"

    @t.case("scaled amounts snap to nice increments, never ugly decimals")
    def _():
        def line(amt, unit, scale):
            return format_ingredient_line(
                {"amount": amt, "unit": unit, "name": "x", "original_text": ""},
                scale,
            )
        # 0.8x is the live household case. None of these may show a raw decimal.
        assert line(4, "cup", 0.8).startswith("**3¼**"), line(4, "cup", 0.8)      # 3.2 -> 3¼
        assert line(0.25, "tsp", 0.8).startswith("**¼**"), line(0.25, "tsp", 0.8)  # 0.2 -> ¼
        assert line(1, "tsp", 0.8).startswith("**¾**"), line(1, "tsp", 0.8)        # 0.8 -> ¾
        assert line(4, "oz", 0.8).startswith("**3**"), line(4, "oz", 0.8)          # 3.2 -> 3 (whole oz)
        for bad in (".2", "3.2", "0.8", "6⅜"):
            assert bad not in line(4, "cup", 0.8)

    @t.case("weights snap to half-pound increments — no 1.25 lb")
    def _():
        def lb(amt, scale):
            return format_ingredient_line(
                {"amount": amt, "unit": "lb", "name": "beef", "original_text": ""},
                scale,
            )
        assert lb(1.25, 1.0).strip() == "**1½** lb beef", lb(1.25, 1.0)
        assert lb(1.75, 1.0).strip() == "**2** lb beef", lb(1.75, 1.0)
        assert lb(0.3, 1.0).strip() == "**½** lb beef", lb(0.3, 1.0)   # floor, never 0

    @t.case("floor keeps a real ingredient from rounding away to zero")
    def _():
        tiny = {"amount": 0.25, "unit": "tsp", "name": "cayenne", "original_text": ""}
        assert format_ingredient_line(tiny, 0.1).strip() == "**¼** tsp cayenne"

    @t.case("unscaled recipe shows original_text verbatim")
    def _():
        assert format_ingredient_line(can_tomato, 1.0) == "1 (28-oz) can"


# ---------------------------------------------------------------------------
# Grocery optional-add-on tests (mealplan/grocery.py)
# ---------------------------------------------------------------------------

def grocery_addon_tests(t: _T):
    from mealplan import grocery

    class _Lib:
        def __init__(self, recipes): self._r = {r["id"]: r for r in recipes}
        def get(self, rid): return self._r.get(rid)

    def ing(name, unit, original="", aisle=""):
        return {"name": name, "unit": unit, "original_text": original or name,
                "aisle": aisle, "amount": 0}

    recipe = {
        "id": "r1", "title": "Test Dish", "servings_original": 4,
        "ingredients": [
            ing("chicken", "lb", "1 lb chicken", "Meat"),          # real, not an addon
            ing("side salad", "serving", "Salad", "Produce"),       # offer
            ing("thyme leaves", "serving", "Fresh thyme leaves for garnish", "Produce"),
            ing("salt and pepper", "serving", "salt and pepper"),   # noise
            ing("olive oil", "serving", "Olive oil"),               # noise (staple+modifier)
            ing("a brush", "serving", "A brush"),                   # noise (equipment)
        ],
    }
    lib = _Lib([recipe])

    @t.case("addon collector offers sides/garnishes, drops staples + equipment")
    def _():
        addons = grocery.collect_optional_addons(["r1"], 4, library=lib)
        names = {a["name"] for a in addons}
        assert names == {"side salad", "thyme leaves"}, names

    @t.case("real-unit ingredients never appear as add-ons")
    def _():
        addons = grocery.collect_optional_addons(["r1"], 4, library=lib)
        assert all(a["name"] != "chicken" for a in addons)

    @t.case("addon collector de-duplicates across recipes")
    def _():
        r2 = dict(recipe, id="r2", title="Other")
        addons = grocery.collect_optional_addons(["r1", "r2"], 4, library=_Lib([recipe, r2]))
        assert sum(a["name"] == "side salad" for a in addons) == 1

    @t.case("addon_to_item produces a hand-off-ready SmartCart Item")
    def _():
        addons = grocery.collect_optional_addons(["r1"], 4, library=lib)
        salad = next(a for a in addons if a["name"] == "side salad")
        item = grocery.addon_to_item(salad)
        assert item["item_name"] == "side salad"
        assert item["quantity"] == 1 and item["unit"] == ""
        assert item["category"] == "Produce" and item["source"] == "meal_plan"


def main() -> int:
    t = _T()
    rules_tests(t)
    planner_tests(t)
    recipe_view_tests(t)
    grocery_addon_tests(t)
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
