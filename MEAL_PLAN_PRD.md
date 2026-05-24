# Meal Planner — Product Requirements Document

**Status**: Approved for build, V1
**Date**: 2026-05-24
**Owner**: Scott Larson (single user)
**Target repo**: [github.com/SLED44/smart-cart](https://github.com/SLED44/smart-cart) (unified with existing SmartCart app)

---

## 0. How to read this doc

This PRD is self-contained. The implementing Claude instance does **not** have access to the discovery conversation that produced it. Every decision is captured here with the reasoning that drove it. File paths and module names refer to the existing SmartCart repo — read [CLAUDE.md](CLAUDE.md) first to understand the current codebase.

Where a decision is intentionally deferred, it is marked **OPEN** and listed in §20 with options.

---

## 1. Executive summary

Add a **Meal Planner** feature to the existing SmartCart Streamlit app. The planner:

- Takes user input "plan N meals" (typically N = 4–5)
- Proposes N recipes from a self-hosted recipe library, respecting a user-defined ruleset (protein limits, cuisine variety, favorites cadence, exclusions, etc.)
- Lets the user keep, swap, or "never make again" each suggestion
- On swap, surfaces 5 alternative candidates (filtered by cuisine + protein, or unfiltered)
- On confirm, aggregates the recipes' ingredients into a structured grocery list and hands it directly to SmartCart's existing preview → match → cart pipeline
- Supports a cooking mode for browsing recipes during the week and capturing "made it / made changes / never again" feedback

The planner is **100% deterministic Python**. It does **not** call the Anthropic API. Recipe sourcing uses **Spoonacular**, with results cached into Supabase. Recipes added from outside Spoonacular (NYT, family recipes) are normalized into the same JSON schema via Claude.ai chat **externally** and pasted into the app — no in-app LLM call.

---

## 2. Context

### 2.1 What SmartCart already is

Single-user Streamlit app at `https://sled44-smartcart.streamlit.app/`. Takes a pasted grocery list, parses + categorizes via Claude Haiku, matches each item against Kroger products + saved household preferences, lets the user review item-by-item, then posts the cart to Kroger (City Market). User completes checkout in the City Market app.

Stack: Streamlit + Anthropic SDK + Kroger Public API (OAuth 2.0 + PKCE) + Supabase Postgres (single `kv (key text PK, value jsonb)` table) + Streamlit Community Cloud hosting. All persistent state flows through [supabase_kv.py](supabase_kv.py) → [preference_store.py](preference_store.py). See [CLAUDE.md](CLAUDE.md) for full module map.

### 2.2 Why bolt the meal planner onto SmartCart

The discovery process compared two architectures: separate Streamlit app sharing the Supabase backend vs. unified single app. **Unified won** for these reasons:

1. The meal-plan grocery output is already byte-compatible with SmartCart's list-parser input format (categorized, with quantities, no brand names). The "handoff" is in-process state injection, not cross-app messaging.
2. SmartCart's backend modules (`list_parser`, `product_matcher`, `sale_scanner`, `cart_manager`, `preference_store`, `kroger_auth`) are already UI-free. `list_parser.py:30-34` even has a comment from an earlier session anticipating a meal-planner module calling it unchanged.
3. Single `APP_PASSWORD` gate already covers both features — no auth boundary to negotiate.
4. Two Streamlit Cloud apps means two URLs, two cold-starts, no shared session state, and a clunky browser-tab-switch hand-off exactly where we want it smooth.
5. The 1500-line `main.py` is already flagged on the SmartCart backlog (CLAUDE.md item #16) for splitting into a `screens/` package. The meal-planner is the right forcing function to do that split.

---

## 3. Goals & non-goals

### 3.1 Goals (V1)

- Replace the manual "what should we eat this week?" decision-making with a rule-respecting planner
- Capture all meal-plan rules in a structured in-app editor that lives in Supabase
- Allow rapid swap-and-replace of individual suggestions
- Track cooking outcomes (made it / made changes / never again) to feed back into future plans
- Hand the resulting grocery list seamlessly into SmartCart's existing flow with no Claude parse step
- Build the library organically: 100+ seed recipes on bootstrap, grows as user swaps and discovers
- Tablet-horizontal as the primary UX target (1024–1280 px)
- Zero recurring Claude API cost on the meal-planner side (Anthropic spend stays at SmartCart's current ~$1–3/month)

### 3.2 Non-goals (V1)

- Multi-week planning view (plan only the current week)
- Plan history / archive UI (engine still tracks history internally — that's data, not UI)
- Per-plan serving-size overrides (cooking for guests — defer to V2)
- Mobile-phone-optimized UX (tablet horizontal is the priority; phone is best-effort)
- Multi-user / per-family-member accounts (same `APP_PASSWORD` model as SmartCart)
- Push notifications, calendar integration, email summaries
- Lunch planning (leftovers cover lunches per family practice)
- Recipe video, ratings/reviews, social features
- Automatic plan generation on a schedule (user always triggers manually)

---

## 4. Users

Single user (`APP_PASSWORD`-gated). Same access model as SmartCart. The meal planner does not introduce any per-user data — everything is keyed at the household level.

Family context (drives planner defaults, editable in Rules screen):
- Household size: 3–4 (default 4)
- Spice tolerance: mild (kid-friendly)
- Cooking appliance preference: air fryer (full-size oven with air-fry setting)
- Sauce philosophy: "buy don't make" (store-bought sauces/dressings preferred; taco seasoning is the exception)
- Leftover behavior: leftovers cover lunches; no lunch planning

---

## 5. End-to-end user flow (the canonical journey)

1. User logs in (`APP_PASSWORD`) from any device, any day of the week.
2. From Home, user clicks **"Plan meals"** and enters **N** (number of recipes — default 5, range 1–7).
3. Planner runs (pure Python) and surfaces N recipe cards as the proposed lineup. Each card shows: title, image, cuisine, protein, prep+cook time, "last made" date if applicable, and a **Keep / Replace** toggle.
4. Optional expander **"Why these picks?"** reveals: protein/cuisine/carb usage wheels, shrimp counter, favorites cadence status, any constraints that were relaxed.
5. Optional global button **"Give me 5 new options"** (or whatever N is) regenerates the entire lineup with the same rules.
6. For each "Replace" tap:
   - User picks cuisine (dropdown) and/or protein (dropdown). Either or both may be left as "any".
   - Optional **name search** field for finding a specific library recipe.
   - Planner returns up to 5 candidate cards.
   - User can: pick one, hit **"Give me 5 new"** (regenerate same filters), or hit **"Never make"** on any candidate (excludes from future planning forever).
   - All 5 surfaced candidates are saved to the library on first view (cache once, free forever).
7. When all N slots are filled, user clicks **Confirm plan**.
8. App writes the plan to `current_plan` KV bucket, updates rule-engine state (last-used dates, shrimp counter, week number), and presents the **"Generate grocery list"** button.
9. Click → pure-Python aggregator collapses N recipes' ingredient lists (dedup by canonical name + unit, sum quantities, scale to household size, group by Spoonacular's `aisle` field).
10. App writes structured items into SmartCart's `st.session_state.parsed_result` and routes the user to SmartCart's **preview** screen.
11. On preview, user can: add staples (existing button), add ad-hoc items via new **"+ Add item"** row (free-text name, qty, unit, category), edit quantities, delete items already on hand.
12. Continue through SmartCart's existing flow: item filter (deselect pantry items) → sale scan → review → cart post.
13. During the week, user opens the app, taps a meal from the active plan, sees the full recipe (ingredients, steps, image, prep/cook time, scaled to household size).
14. After cooking, user taps one of: **Made it** (increments `times_cooked`, sets `last_cooked`), **Made changes** (opens text field — appends a dated note to the recipe's `user_notes`), **Never again** (adds recipe to exclusions; planner never surfaces it).

---

## 6. Two-phase workflow

Mirrors the workflow already authored in the user's existing rules doc:

**Phase 1 — Propose & Confirm** (§5 steps 1–7)
- Pure-code planner proposes lineup
- User swaps individual meals or regenerates whole lineup
- No grocery list generated until lineup confirmed

**Phase 2 — Execute** (§5 steps 8–14)
- Confirmed plan persists, rule state updates
- Grocery list aggregator runs
- Hand-off into SmartCart's existing pipeline for cart construction
- Cooking-mode views and feedback flags update library

---

## 7. Data model

All data persists in the existing Supabase `kv (key text PK, value jsonb)` table. No schema changes. New keys added by this feature:

| Key | Type | Description |
|---|---|---|
| `meal_plan_rules` | object | Full ruleset (§7.1) |
| `recipe_library` | object | Map of `recipe_id → Recipe` (§7.2) |
| `current_plan` | object | Active week's confirmed plan (§7.3) |
| `meal_plan_history` | array | Rolling log of past confirmed plans (§7.4) |
| `pending_lineup` | object | In-progress (unconfirmed) lineup — survives page reload |
| `spoonacular_bootstrap_state` | object | One-shot bootstrap progress tracker |

### 7.1 Rules schema (`meal_plan_rules`)

```json
{
  "household": {
    "size": 4,
    "meals_per_week_default": 5,
    "spice": "mild",
    "default_appliance": "air_fryer",
    "buy_dont_make_sauces": true
  },
  "protein_limits": {
    "beef":   { "max_per_week": 1, "absolute_ceiling": 2 },
    "pork":   { "max_per_week": null, "absolute_ceiling": null },
    "chicken":{ "max_per_week": null, "absolute_ceiling": null },
    "fish":   { "max_per_week": null, "absolute_ceiling": null },
    "lamb":   { "max_per_week": null, "absolute_ceiling": null },
    "shrimp": { "max_per_week": 1, "absolute_ceiling": 1 },
    "plant":  { "max_per_week": 1, "absolute_ceiling": 1 }
  },
  "protein_cadences": [
    { "protein": "shrimp", "cadence_weeks": 4, "last_used_week": 3 }
  ],
  "carb_limits": {
    "rice":  2,
    "pasta": null,
    "bread": null,
    "grain": null,
    "potato":null,
    "salad": 2
  },
  "cuisines": {
    "rotation_set": ["american","italian","mexican","japanese","korean","vietnamese","thai","chinese","mediterranean","greek","middle_eastern","moroccan","indian"],
    "must_include_one_of_per_week": ["american","italian","mexican"],
    "forbid_back_to_back_same_cuisine": true
  },
  "favorites": [
    { "recipe_id": "lib_smash_burgers",         "cadence_weeks": [4,6], "last_used_week": 1 },
    { "recipe_id": "lib_chicken_katsu",         "cadence_weeks": [4,6], "last_used_week": 4 },
    { "recipe_id": "lib_miso_glazed_salmon",    "cadence_weeks": [4,6], "last_used_week": null },
    { "recipe_id": "lib_greek_lamb_meatballs",  "cadence_weeks": [4,6], "last_used_week": null },
    { "recipe_id": "lib_moroccan_pork_tenderloin","cadence_weeks": [4,6],"last_used_week": null },
    { "recipe_id": "lib_tofu_banh_mi_bowls",    "cadence_weeks": [4,6], "last_used_week": null }
  ],
  "exclusions": [
    "lib_honey_garlic_salmon"
  ],
  "pair_exclusions": [
    ["lib_smash_burgers", "lib_chicken_katsu"]
  ],
  "state": {
    "current_week": 6,
    "shrimp_counter": 2,
    "last_plan_confirmed_at": null
  }
}
```

Field semantics:
- `max_per_week` = soft target the planner respects unless no candidate fits
- `absolute_ceiling` = never exceed, even under relaxation
- `cadence_weeks: [4,6]` for favorites = planner prioritizes after 4 weeks, force-includes at 6 (subject to hard rules)
- `current_week` = integer that increments by 1 on each plan confirmation
- "plant" protein covers tofu, chickpeas, lentils, beans; combined with the vegetarian limit
- `null` in `protein_limits` / `carb_limits` = no limit

### 7.2 Recipe schema (`recipe_library[recipe_id]`)

```json
{
  "id": "lib_smash_burgers",
  "source": "spoonacular | claude_chat | user_manual",
  "source_id": "12345",
  "source_url": "https://...",
  "title": "Classic Smash Burgers",
  "image_url": "https://spoonacular.com/...",
  "servings_original": 4,
  "ready_in_minutes": 25,
  "prep_minutes": 10,
  "cook_minutes": 15,
  "cuisines": ["american"],
  "diet_tags": [],
  "dish_types": ["main course"],
  "equipment": ["air_fryer","sheet_pan"],
  "proteins": ["beef"],
  "carbs": ["bread"],
  "ingredients": [
    {
      "name": "ground beef (80/20)",
      "amount": 1.5,
      "unit": "lbs",
      "aisle": "Meat",
      "original_text": "1.5 lbs 80/20 ground beef"
    }
  ],
  "instructions": [
    { "step_number": 1, "text": "Preheat sheet pan at 500°F with air-fry setting for 10 minutes." }
  ],
  "user_notes": "",
  "times_cooked": 3,
  "last_cooked_at": "2026-04-10T19:00:00Z",
  "status": "active",
  "added_at": "2026-05-24T08:00:00Z"
}
```

`status` values:
- `active` — normal, eligible
- `favorite` — appears in `meal_plan_rules.favorites` (denormalized convenience flag)
- `never_again` — excluded from planner forever (denormalized; canonical source is `meal_plan_rules.exclusions`)

### 7.3 Current plan schema (`current_plan`)

```json
{
  "week_number": 6,
  "confirmed_at": "2026-05-24T10:30:00Z",
  "meals": [
    { "recipe_id": "lib_smash_burgers", "added_via": "favorite" },
    { "recipe_id": "lib_miso_glazed_salmon", "added_via": "favorite" },
    { "recipe_id": "lib_moroccan_pork_tenderloin", "added_via": "favorite" },
    { "recipe_id": "lib_greek_lamb_meatballs", "added_via": "favorite" },
    { "recipe_id": "lib_tofu_banh_mi_bowls", "added_via": "favorite" }
  ],
  "grocery_list_generated_at": null
}
```

`added_via` values: `favorite`, `proposal`, `swap_filtered`, `swap_unfiltered`, `manual_search`.

### 7.4 Plan history schema (`meal_plan_history`)

Array of past `current_plan` objects, capped at the last 26 weeks (~6 months). Each entry retains the `meals` list and `confirmed_at`. Used by the rules engine to check `last_used_week` and to populate "last made" dates on recipe cards. Not exposed via a UI in V1.

### 7.5 Pending lineup (`pending_lineup`)

Same shape as `current_plan` but without `confirmed_at`. Persisted between page reloads so users don't lose their in-progress curation. Cleared on confirmation or explicit reset.

---

## 8. Rules engine specification

Module: **`mealplan/rules.py`** (new).

### 8.1 Public interface

```python
def evaluate_candidate(
    recipe: Recipe,
    rules: Rules,
    current_lineup: list[Recipe],
    history: list[Plan],
    relaxation_level: int = 0
) -> Evaluation:
    """
    Returns:
      Evaluation(
        eligible: bool,
        score: float,           # higher = better fit
        reasons: list[str],     # human-readable, for "Why these picks?" panel
        relaxations_applied: list[str]
      )
    """
```

### 8.2 Evaluation order (at relaxation_level = 0, no relaxation)

For each candidate recipe, check in order. First fail short-circuits to `eligible = False`:

1. **Hard exclusions**: `recipe.id in rules.exclusions` → ineligible
2. **Status check**: `recipe.status == "never_again"` → ineligible
3. **Pair-exclusion**: any `(recipe.id, other.id)` in `pair_exclusions` where `other` is already in `current_lineup` → ineligible
4. **Absolute protein ceiling**: count proteins in `current_lineup`; if adding this recipe exceeds any `absolute_ceiling` → ineligible
5. **Vegetarian cap**: count plant-protein recipes; ≥ `protein_limits.plant.absolute_ceiling` (1) → ineligible if this is also plant
6. **Shrimp cadence**: if recipe is shrimp and `current_week - last_used_week < cadence_weeks` → ineligible
7. **Spice level**: title contains "spicy", "hot", "fire", "ghost", "habanero" (case-insensitive) AND household spice = "mild" → ineligible

Then compute `score`:
- Base = 100
- +30 if recipe is a favorite and `current_week - last_used_week >= cadence_weeks[0]`
- +50 if recipe is a favorite and `current_week - last_used_week >= cadence_weeks[1]` (force-include territory)
- +20 if `recipe.cuisines` includes a cuisine in `must_include_one_of_per_week` and none of `current_lineup` has covered it yet
- −20 if cuisine of this recipe is already represented in `current_lineup` (variety bias)
- −15 if any protein of this recipe is already in `current_lineup`
- −15 if any carb of this recipe is already in `current_lineup` (especially rice — see #9)
- −30 if adding this recipe would exceed a `max_per_week` (still eligible at relaxation 0 IF under ceiling, but heavily penalized)
- −40 if recipe was used within the last 4 weeks per `history` (not a hard rule but discouraged)

### 8.3 Relaxation order **[OPEN — see §20.1]**

When the planner cannot fill all N slots, increment `relaxation_level` and retry. Each level loosens specific checks:

Provisional ordering (subject to validation in §20.1):
- **Level 1**: ignore variety penalties (don't penalize same protein/cuisine/carb)
- **Level 2**: ignore favorites cadence (don't force-include due favorites)
- **Level 3**: ignore the "must include American/Italian/Mexican" cuisine requirement
- **Level 4**: allow `max_per_week` to be exceeded (still subject to `absolute_ceiling`)

**Never relaxed at any level**: `exclusions`, `pair_exclusions`, `absolute_ceiling`, vegetarian cap, shrimp cadence, spice level, `never_again` status.

Each relaxation applied is logged on the candidate's `reasons` and surfaced in the "Why these picks?" panel.

### 8.4 Initial state inputs

The engine reads `meal_plan_rules.state.current_week` to determine "this week" for cadence checks. The week number increments by 1 on each plan confirmation (it is NOT a calendar week). This matches the user's existing mental model from the source rules doc.

---

## 9. Planner algorithm

Module: **`mealplan/planner.py`** (new).

```
function generate_lineup(N: int, rules: Rules, library: Library, history: list[Plan]) -> list[Recipe]:
    lineup = []
    relaxation = 0

    while len(lineup) < N:
        candidates = []
        for recipe in library.all_active():
            eval = rules.evaluate_candidate(recipe, rules, lineup, history, relaxation)
            if eval.eligible:
                candidates.append((eval.score, recipe))

        if not candidates:
            relaxation += 1
            if relaxation > 4:
                raise NoCandidatesError(slot_index=len(lineup))
            continue

        # Sort by score desc, then by least-recently-used (tie-breaker)
        candidates.sort(key=lambda c: (-c[0], c[1].last_cooked_at or "0000"))
        chosen = candidates[0][1]
        lineup.append(chosen)
        relaxation = 0  # reset for next slot

    return lineup
```

### 9.1 Library-only path (default)

V1 generates lineups exclusively from the saved library (which after bootstrap contains 100+ recipes). The planner does NOT call Spoonacular during normal `generate_lineup`.

### 9.2 Spoonacular fallback (swap-time only)

When the user invokes a Replace with cuisine + protein filters and the library yields fewer than 5 candidates after rules evaluation, the planner queries Spoonacular for additional candidates with those filters, saves them to the library, then re-runs evaluation. This grows the library organically as the user explores.

### 9.3 Whole-plan "Give me 5 new options" button

Re-runs `generate_lineup(N, ...)` with the current lineup pinned as "recently used" — meaning recipes from the previous proposal get a temporary −40 score penalty so they're unlikely to reappear. The penalty is in-memory only; it doesn't persist.

---

## 10. Swap mechanic specification

Module: **`mealplan/swap.py`** (new).

### 10.1 Inputs

- `slot_index` — which meal in the current lineup is being swapped
- `cuisine_filter` — string or `None`
- `protein_filter` — string or `None`
- `name_search` — string or `None`
- `seen_candidate_ids` — set of recipe IDs already shown for this slot (so "Give me 5 new" doesn't repeat)

### 10.2 Algorithm

```
function get_swap_candidates(slot_index, cuisine, protein, name_search, seen_ids) -> list[Recipe]:
    # Step 1: filter library
    pool = library.filter(cuisine=cuisine, protein=protein, name_search=name_search)
    pool = [r for r in pool if r.id not in seen_ids]

    # Step 2: evaluate against rules with current lineup minus the slot being swapped
    eval_lineup = current_lineup_without_slot(slot_index)
    eligible = [r for r in pool if rules.evaluate_candidate(r, rules, eval_lineup, history).eligible]

    # Step 3: if fewer than 5 eligible, query Spoonacular
    if len(eligible) < 5 and cuisine and protein:
        new = spoonacular.search(cuisine=cuisine, protein=protein, max_ready=60, mild=True)
        for r in new:
            library.save(r)  # cache immediately
            if r.id not in seen_ids:
                eligible.append(r)

    # Step 4: sort by score, return top 5
    return sort_and_top_n(eligible, n=5)
```

### 10.3 UX on candidate cards

Each candidate card shows: title, image, cuisine, protein, prep+cook time, **last made** (date or "never"), **user notes** (truncated if long), and three actions:
- **Pick** — fills the slot
- **Skip** (implicit — just show the next 5)
- **Never make** — marks recipe `never_again`, removes from candidates immediately

---

## 11. Cooking mode specification

Module: **`screens/mealplan_cook.py`** (new) — wired into the Active Plan screen.

### 11.1 Recipe view

Single read-only page rendered with the design system. Layout:

- Hero image (Spoonacular URL, fallback to placeholder)
- Title, cuisine pills, prep/cook time, servings (displayed scaled to household size)
- Ingredients section: bullet list, quantities scaled to household size
- Instructions section: numbered list (Spoonacular's structured steps)
- User notes (if any), pinned at top of ingredients as a yellow callout
- Three large buttons at the bottom: **Made it**, **Made changes**, **Never again**

### 11.2 Feedback actions

**Made it**:
- `recipe.times_cooked += 1`
- `recipe.last_cooked_at = now()`
- Toast confirmation, return to Active Plan screen

**Made changes**:
- Modal opens with a text input prefilled with the existing `user_notes`
- On save: replaces `user_notes` (V1 keeps it as a single field; multi-note log is V2)
- Also bumps `times_cooked` and `last_cooked_at` (assumes you cooked it if you noted changes)

**Never again**:
- Confirmation dialog
- On confirm: `recipe.status = "never_again"`, append `recipe.id` to `meal_plan_rules.exclusions`
- Recipe disappears from future planning instantly

### 11.3 Out-of-order browsing

The Active Plan screen lists all N meals from `current_plan.meals` as tappable cards. User can open any in any order ("won't necessarily be in the order planned"). No "today's meal" highlighting in V1.

---

## 12. Grocery list aggregator specification

Module: **`mealplan/grocery.py`** (new).

### 12.1 Inputs

- `recipe_ids: list[str]` — the confirmed meal IDs
- `household_size: int` — from rules (default 4)
- `library: Library`

### 12.2 Algorithm

```
function aggregate_grocery_list(recipe_ids, household_size, library) -> list[Item]:
    raw_items = []
    for recipe_id in recipe_ids:
        recipe = library.get(recipe_id)
        scale = household_size / recipe.servings_original
        for ing in recipe.ingredients:
            raw_items.append({
                "name": canonicalize_name(ing.name),
                "amount": ing.amount * scale,
                "unit": ing.unit,
                "aisle": ing.aisle,
                "original_text": ing.original_text,
                "source_recipe": recipe.title,
            })

    # Dedup + sum by (name, unit)
    merged = {}
    for item in raw_items:
        key = (item["name"], item["unit"])
        if key in merged:
            merged[key]["amount"] += item["amount"]
            merged[key]["source_recipes"].append(item["source_recipe"])
        else:
            merged[key] = {**item, "source_recipes": [item["source_recipe"]]}

    # Map Spoonacular aisles to SmartCart categories
    for item in merged.values():
        item["category"] = map_aisle_to_category(item["aisle"])

    # Sort by category order matching rules doc (§7a)
    return sorted(merged.values(), key=category_sort_key)
```

### 12.3 Aisle → category mapping

Maps Spoonacular's `aisle` field (free-form like "Milk, Eggs, & Other Dairy") to the user's 8-category schema from the rules doc:

| Spoonacular aisle (examples) | Category |
|---|---|
| Produce | Produce |
| Meat, Seafood | Meat |
| Milk Eggs and Other Dairy, Cheese | Dairy |
| Frozen | Frozen |
| Spices and Seasonings, Canned and Jarred, Pasta and Rice, Baking | Pantry |
| Beverages, Tea and Coffee, Alcoholic Beverages | Beverages |
| Bread, Bakery/Bread | Bakery |
| Cleaning Products, Paper Goods | Household |
| (unmapped) | Pantry (default) |

A full mapping table lives in `mealplan/grocery.py:AISLE_MAP`. The mapping is editable as a Python constant — adding new aisle entries is a code change but trivial.

### 12.4 Unit canonicalization (V1 scope)

Minimal: sum quantities only when units match exactly. If two recipes call for "eggs" with unit "count" (12 from one, 4 from another), sum to 16. If units differ ("1 cup milk" + "2 tbsp milk"), keep as separate line items — V1 does not convert units. User reviews on preview screen.

---

## 13. SmartCart integration spec

### 13.1 Hand-off mechanism

After §12 produces the structured `list[Item]`, the meal-planner module:

1. Writes to `st.session_state.parsed_result`, conforming to the shape produced by [list_parser.py](list_parser.py)'s `parse_grocery_list()`:
   ```python
   {
     "items": [...],  # the aggregated structured items
     "raw_text": "<generated-from-recipes>",
     "item_count": len(items),
     "parse_warnings": []
   }
   ```
2. Sets `st.session_state.staples_added = False` so the staples prompt still fires
3. Sets `st.session_state.combined_items = items.copy()`
4. Routes to SmartCart's existing `preview` screen via `_go("preview")`

SmartCart's `screen_preview` (currently in `main.py`, post-split in `screens/preview.py`) renders the list normally. No code in SmartCart's review/match/cart pipeline changes.

### 13.2 New SmartCart preview-screen capability

Add a **"+ Add item"** row at the bottom of the preview screen's item table. Fields: name, quantity, unit, category dropdown. Submit appends a new item dict to `st.session_state.combined_items` with `source = "manual"`. Useful for:
- Meal-plan grocery list + an extra item not from any recipe (wine, paper towels)
- Pure SmartCart-only flows that previously required pasting and re-parsing

Implementation: small expander or modal at the bottom of `screen_preview`'s item list. ~50 LOC.

Backward-compat note: items added this way bypass `list_parser` (no Claude call). Category is whatever the user picked; no auto-inference. This is the desired behavior for both flows.

### 13.3 What's reused unchanged

- [product_matcher.py](product_matcher.py) — Kroger search + Claude best-match (existing per-item LLM calls remain)
- [sale_scanner.py](sale_scanner.py) — pre-review sale alts
- [cart_manager.py](cart_manager.py) — posts to Kroger cart
- [preference_store.py](preference_store.py) — pref/staples/session-log
- [kroger_auth.py](kroger_auth.py) — OAuth
- [supabase_kv.py](supabase_kv.py) — KV layer
- [sc_design.py](sc_design.py) + [style.css](style.css) — design system (extend with meal-plan-specific helpers)

### 13.4 What gets touched in existing SmartCart

- `main.py` → split into `screens/` package (long overdue, CLAUDE.md backlog #16)
- `screens/preview.py` (post-split) → add "+ Add item" row
- `screens/home.py` (post-split) → add **"Plan meals"** card to the home screen
- `screens/__init__.py` → add new meal-plan screen imports

---

## 14. Spoonacular API integration

Module: **`mealplan/spoonacular.py`** (new).

### 14.1 Endpoints used

| Endpoint | Purpose | Approx. cost (points) |
|---|---|---|
| `GET /recipes/complexSearch` | Bootstrap pulls, swap-time fallback queries | 1 base + 1 per result + `addRecipeInformation=true` extras |
| `GET /recipes/{id}/information` | Full recipe details if not already fetched | 1 |
| `GET /recipes/{id}/analyzedInstructions` | Structured steps | 0 (included in info) |

Always include: `addRecipeInformation=true`, `instructionsRequired=true`, `addRecipeNutrition=false`, `fillIngredients=true`.

### 14.2 Filters used

- `cuisine` (single or comma-joined)
- `type` = `main course`
- `maxReadyTime` = 60
- `intolerances` — not used in V1 (no household allergies declared)
- `diet` — populated when planner is filling a vegetarian slot
- `excludeIngredients` — used to enforce exclusions at API level when possible
- `sort` = `popularity` (bootstrap) or `random` (swap regenerate)
- `number` — typically 5–10

### 14.3 Image handling

V1 hotlinks Spoonacular's CDN URLs (saved in `recipe.image_url`). Zero storage cost. If Spoonacular ever changes their image hosting we'll fall back to a placeholder. **V2 candidate**: download images into Supabase Storage for durability.

### 14.4 Secrets

`SPOONACULAR_API_KEY` lives in:
- `.env` for local dev (gitignored)
- Streamlit Cloud secrets for production

Same pattern as `ANTHROPIC_API_KEY` and `KROGER_CLIENT_*`. `main.py`'s `st.secrets → os.environ` bridge picks it up automatically.

### 14.5 Rate-limit awareness

Spoonacular free tier = 150 points/day. The bootstrap pull is the biggest single user (~115 points). Track daily usage in `spoonacular_bootstrap_state.points_used_today` so the user can see headroom before triggering further pulls. If a swap-time query would exceed the daily cap, surface a warning and fall back to library-only.

---

## 15. Bootstrap procedure

Module: **`mealplan/bootstrap.py`** (new). Surfaced via a one-time "Bootstrap" screen (also accessible from Rules screen for re-runs).

### 15.1 Steps

1. **Six fan favorites** — for each of: Smash Burgers, Chicken Katsu, Miso Glazed Salmon (soba), Greek Lamb Meatballs (flatbread), Moroccan Pork Tenderloin (couscous), Tofu Banh Mi Bowls (rice vermicelli):
   - Query Spoonacular by recipe title
   - Show top 3 results to user
   - User picks the canonical version (or selects "none of these — I'll paste-import")
   - Save chosen recipe with `id = "lib_<slug>"`, `status = "favorite"`
   - Cost: ~12 points
2. **Cuisine sweep** — for each of the 13 cuisines in `rotation_set`:
   - Query `complexSearch` with `cuisine=X, type=main course, maxReadyTime=60, sort=popularity, number=7`
   - Save all results to library
   - Cost: ~91 points (13 × 7)
3. **Protein gap-fill** — count proteins represented in the library so far. For each under-represented protein (default threshold: <5 recipes), pull additional recipes:
   - Query `complexSearch` with `includeIngredients=<protein>, type=main course, maxReadyTime=60, number=5`
   - Save results
   - Cost: ~10–25 points depending on gaps
4. **Spice filter post-pass** — scan all saved recipes; mark any with "spicy", "hot", "fire", "ghost", "habanero" in title for user confirmation before final save (rules-doc says household is mild).

**Total estimated cost**: ~115 points. Fits in a single free-tier day.

### 15.2 Bootstrap UI

Bootstrap screen shows:
- Estimated cost in Spoonacular points
- Today's remaining free-tier headroom
- Tunable parameters: recipes per cuisine (default 7), max ready time (default 60), cuisine list (multi-select with all 13 pre-checked)
- **Start bootstrap** button
- Live progress with per-cuisine status as pulls complete

Re-runnable: future runs add to the library without removing existing recipes (idempotent on Spoonacular ID).

### 15.3 Current-state importer

Separate "Import current state" screen accessible during bootstrap or from Rules screen. User pastes the "Current State" section of their existing rules doc (or just the values). Importer parses with regex/string parsing (no LLM) and populates `meal_plan_rules.state`:

- `current_week`
- `shrimp_counter`
- `favorites[*].last_used_week` (mapped from "Last used Week N" sentences)
- Any pending lineup → `pending_lineup`

If parsing fails for any field, surface what was captured and let user fix manually.

---

## 16. Screens enumeration

New Streamlit screens (each becomes a file in `screens/`):

| Screen | Module | Role |
|---|---|---|
| `mealplan_home` | `screens/mealplan_home.py` | Entry card on existing SmartCart home; "Plan meals" button + N input |
| `mealplan_propose` | `screens/mealplan_propose.py` | Proposed lineup with per-meal Keep/Replace + "Give me 5 new options" + "Why these picks?" expander |
| `mealplan_swap` | `screens/mealplan_swap.py` | Per-slot candidate picker: cuisine/protein filters + name search + 5 candidate cards |
| `mealplan_active` | `screens/mealplan_active.py` | The confirmed week — list of N meals, tappable to open cooking view |
| `mealplan_cook` | `screens/mealplan_cook.py` | Single-recipe read-only view with made-it / made-changes / never-again buttons |
| `mealplan_rules` | `screens/mealplan_rules.py` | Structured editor for `meal_plan_rules` (§17 details) |
| `mealplan_library` | `screens/mealplan_library.py` | Browse/search/edit the recipe library |
| `mealplan_bootstrap` | `screens/mealplan_bootstrap.py` | One-time + re-runnable Spoonacular pull |
| `mealplan_paste_recipe` | `screens/mealplan_paste_recipe.py` | Paste a JSON recipe from Claude.ai chat → save to library |
| `mealplan_state_import` | `screens/mealplan_state_import.py` | Paste rules-doc "Current State" section → populate engine state |

Existing SmartCart screens after the split:

| Screen | Module |
|---|---|
| `login`, `home`, `preview`, `item_filter`, `sale_scan`, `review`, `summary`, `preferences`, `staples`, `store_setup` | `screens/login.py`, `screens/home.py`, … |

Router stays in `main.py`, dispatching to imported `screen_*` functions.

---

## 17. Rules editor screen — UI specification

`screens/mealplan_rules.py` renders the full rules schema (§7.1) as a single scrollable page with collapsible sections matching the rules-doc structure:

| Section | Inputs |
|---|---|
| **Family & Defaults** | Household size (number stepper), Default meals/week (number stepper), Spice level (dropdown), Default appliance (dropdown), Buy-don't-make-sauces (toggle) |
| **Protein limits** | Table: row per protein (beef/pork/chicken/fish/lamb/shrimp/plant), columns: max_per_week (number or "—"), absolute_ceiling (number or "—") |
| **Protein cadences** | Editable list of `{protein, cadence_weeks, last_used_week}` rows with + Add / × Remove |
| **Carb limits** | Table similar to proteins, one row per carb category |
| **Cuisine variety** | Multi-select for `rotation_set` (preset to 13), multi-select for `must_include_one_of_per_week`, toggle for `forbid_back_to_back_same_cuisine` |
| **Favorites** | Table: row per favorite, columns: Recipe (autocomplete searching library), Cadence weeks min/max (two number inputs), Last used week (read-only or editable) |
| **Exclusions** | List: each row is a recipe name (autocomplete from library), with × Remove |
| **Pair-exclusions** | List: each row is a pair of recipe names with × Remove |
| **Current state** | Read-only display of `state.*` fields. "Reset state" button (with confirmation) sets `current_week=1`, clears counters, clears `last_used_week`. |

Save button at the top + bottom writes the full blob to `kv_put("meal_plan_rules", ...)`. Validation: per-field client-side (numeric ranges, non-empty cuisine lists). Server-side validation in `mealplan/rules.py:validate_rules()` before save.

---

## 18. Library browser screen — UI specification

`screens/mealplan_library.py`:

- Top bar: search input (name search), cuisine multi-filter, protein multi-filter, status filter (active / favorite / never_again)
- Result grid: recipe cards (~3 per row at tablet horizontal)
- Each card: image, title, cuisines, proteins, ready time, last-cooked date, times-cooked count, status badge
- Click card → opens read-only cooking-mode view (§11) with added admin controls: **Edit notes**, **Toggle favorite**, **Toggle never-again**, **Delete from library**
- "+ Paste recipe from Claude" button → routes to `mealplan_paste_recipe`
- "Bootstrap more recipes" button → routes to `mealplan_bootstrap`

---

## 19. Architecture & file map

### 19.1 Repo layout (post-split)

```
/
├── main.py                          # router only (~150 LOC after split)
├── screens/
│   ├── __init__.py
│   ├── login.py
│   ├── home.py
│   ├── preview.py                   # extend with "+ Add item" row
│   ├── item_filter.py
│   ├── sale_scan.py
│   ├── review.py
│   ├── summary.py
│   ├── preferences.py
│   ├── staples.py
│   ├── store_setup.py
│   ├── mealplan_home.py             # NEW
│   ├── mealplan_propose.py          # NEW
│   ├── mealplan_swap.py             # NEW
│   ├── mealplan_active.py           # NEW
│   ├── mealplan_cook.py             # NEW
│   ├── mealplan_rules.py            # NEW
│   ├── mealplan_library.py          # NEW
│   ├── mealplan_bootstrap.py        # NEW
│   ├── mealplan_paste_recipe.py     # NEW
│   └── mealplan_state_import.py     # NEW
├── mealplan/                        # NEW package
│   ├── __init__.py
│   ├── rules.py                     # Rule engine + validation
│   ├── planner.py                   # Lineup generation
│   ├── swap.py                      # Candidate picker
│   ├── grocery.py                   # Ingredient aggregator + aisle map
│   ├── spoonacular.py               # API client
│   ├── library.py                   # Recipe CRUD over kv
│   └── bootstrap.py                 # One-shot pull
├── cart_manager.py                  # unchanged
├── kroger_auth.py                   # unchanged
├── list_parser.py                   # unchanged
├── preference_store.py              # unchanged
├── product_matcher.py               # unchanged
├── sale_scanner.py                  # unchanged
├── sc_design.py                     # extend with meal-plan helpers
├── style.css                        # extend with meal-plan CSS
├── supabase_kv.py                   # unchanged
└── …                                # existing files
```

### 19.2 main.py post-split

```python
import streamlit as st
from screens import (
    login, home, preview, item_filter, sale_scan, review, summary,
    preferences, staples, store_setup,
    mealplan_home, mealplan_propose, mealplan_swap, mealplan_active,
    mealplan_cook, mealplan_rules, mealplan_library, mealplan_bootstrap,
    mealplan_paste_recipe, mealplan_state_import,
)

SCREENS = {
    "login": login.render, "home": home.render, "preview": preview.render,
    # ... etc
    "mealplan_home": mealplan_home.render,
    # ...
}

def main():
    _init_state()
    SCREENS[st.session_state.screen]()
```

Each screen module exposes `def render():` as its public interface.

### 19.3 Supabase

No schema changes. New `kv` keys (listed in §7). All access flows through `mealplan/library.py` (recipes) and direct `supabase_kv` calls for rules/state.

### 19.4 Streamlit Cloud impact

- One additional environment variable (`SPOONACULAR_API_KEY`)
- Cold start unchanged (Spoonacular client lazy-loaded)
- Bundle size +trivial (new Python modules, no new heavy deps; `requests` already vendored)

### 19.5 Streamlit CSS gotchas reminder

Per CLAUDE.md, three traps to avoid when writing new screens:
1. Use `st.html()` not `st.markdown()` for raw HTML/CSS
2. Escape `</style>` in CSS file content with `.replace("</style>", "<\\/style>")`
3. `:root` CSS vars don't propagate; use inline literal colors (or var() under a `.stApp` scope if that's been fixed)

---

## 20. Open implementation questions

### 20.1 Constraint relaxation order

The provisional order in §8.3 is the author's first cut. The implementing Claude should validate by:

1. Running the rules engine in dry-run mode against a synthetic library of 30 recipes and confirming the relaxation order produces sensible lineups in edge cases (e.g., "only beef recipes available" — should the engine relax the beef cap before failing? Probably yes up to ceiling 2.)
2. Returning to the user for sign-off on the final order, with a few concrete edge-case examples.

Options to consider:
- **A (provisional)**: relax variety → favorites cadence → cuisine-must-include → soft caps. Never relax exclusions / pair-exclusions / absolute ceilings.
- **B (stricter)**: never relax soft caps; if no candidate fits, return fewer than N meals with a UI warning and let user manually add the last slot from the library browser.
- **C (looser)**: allow relaxation of cuisine-must-include before favorites cadence (favorites are more important than weekly cuisine spread).

### 20.2 "Made changes" notes — V2 evolution

V1 stores a single `user_notes` string. V2 candidates:
- Append dated entries instead of replacing
- Separate fields for "permanent recipe edit" vs. "one-time note"
- "Edit the recipe" mode that modifies ingredient quantities/instructions directly

The implementing Claude should leave the data model open to evolution (e.g., store as `user_notes` string but use a serialization path that could later become a list).

### 20.3 Pantry awareness

The current grocery flow has users manually deselect items they already have (item_filter screen). A V2 enhancement could track an explicit `pantry` KV bucket and auto-deselect known-stocked items. Out of scope for V1 but worth not painting the data model into a corner.

### 20.4 Spoonacular failure modes

What if Spoonacular is down during a swap query?
- Fall back to library-only candidates
- Surface a small banner "Spoonacular unavailable; showing library matches only"
- Never block the user from finalizing a plan

### 20.5 Re-running bootstrap

Idempotency: a re-run should not duplicate recipes (key on Spoonacular ID). Recipes already in the library with a `user_notes` field or `times_cooked > 0` should never be overwritten by bootstrap.

---

## 21. Cost & operational impact

| Item | Before | After (V1) |
|---|---|---|
| Anthropic API | $1–3/mo (SmartCart parsing + matching) | Unchanged — no LLM in meal-planner path |
| Kroger API | Free tier (<1k of 500k credits) | Unchanged |
| Supabase | Free tier (<1 MB of 500 MB) | ~2–5 MB for ~150-recipe library; still well under cap |
| Spoonacular API | $0 | $0 in steady state; ~$0–2 for bootstrap if rushed |
| Streamlit Cloud | Free | Unchanged |
| **Total monthly** | **~$1–3** | **~$1–3** |

Anthropic spending cap recommendation unchanged: $10/month ceiling at console.anthropic.com.

---

## 22. Suggested implementation phasing

Suggested order. Each phase can ship and be tested independently.

1. **Phase 0 — Repo prep**: split `main.py` into `screens/`, add `mealplan/` package skeleton, add `SPOONACULAR_API_KEY` to secrets templates, verify nothing in SmartCart broke. Land this as one PR.
2. **Phase 1 — Data layer + Spoonacular client**: build `mealplan/spoonacular.py` and `mealplan/library.py`. Hand-test pulls via a CLI script. No UI yet.
3. **Phase 2 — Rules engine + planner**: build `mealplan/rules.py` and `mealplan/planner.py`. Unit tests with mocked library. Validate relaxation order with synthetic data (re-engage user per §20.1).
4. **Phase 3 — Rules editor screen + state importer**: build `screens/mealplan_rules.py` and `screens/mealplan_state_import.py`. User pastes rules + state, screens save to KV.
5. **Phase 4 — Bootstrap**: build `screens/mealplan_bootstrap.py` and `mealplan/bootstrap.py`. Run it end-to-end with user, populate ~110 recipes.
6. **Phase 5 — Library browser + paste-recipe**: build `screens/mealplan_library.py` and `screens/mealplan_paste_recipe.py`. User can now browse, search, manually add recipes.
7. **Phase 6 — Propose + swap + active plan**: build `screens/mealplan_home.py`, `mealplan_propose.py`, `mealplan_swap.py`, `mealplan_active.py`. User can plan a week end-to-end.
8. **Phase 7 — Cooking mode**: build `screens/mealplan_cook.py` with made-it/made-changes/never-again. Feedback loop closed.
9. **Phase 8 — Grocery aggregator + SmartCart hand-off**: build `mealplan/grocery.py`, extend `screens/preview.py` with "+ Add item" row, wire the confirmation flow. End-to-end meal-plan → cart works.
10. **Phase 9 — Polish + tablet pass**: design system extensions, `style.css` updates for tablet-horizontal breakpoints, verify all screens on a real tablet.

Each phase has a clear acceptance criterion (UI ships, data round-trips, user can complete the workflow). The implementing Claude should propose acceptance tests per phase before writing code.

---

## 23. Glossary

| Term | Meaning |
|---|---|
| **Lineup** | The N proposed recipes (pre-confirmation) |
| **Plan** | A confirmed lineup; lives in `current_plan` until next confirmation, then moves to `meal_plan_history` |
| **Library** | The persisted set of all recipes (`recipe_library` KV key) |
| **Bootstrap** | One-time pull of ~110 seed recipes from Spoonacular |
| **Wheel** | A color-coded usage indicator for proteins/cuisines/carbs over the recent rolling window |
| **Cadence** | The "every N weeks" return interval for favorite recipes |
| **Slot** | One position in the lineup (slot 0 through slot N−1) |
| **Swap** | Replacing a single slot's recipe with a different one |
| **Relaxation** | Loosening soft constraints when no candidate satisfies all rules |

---

## 24. Sign-off

This PRD reflects discovery conversation through 2026-05-24. Owner has approved scope and architecture. Implementing Claude instance should:

1. Read CLAUDE.md and README.md first
2. Skim this PRD start-to-finish
3. Re-engage owner per §20.1 (constraint relaxation) and any other questions surfaced during Phase 0 / Phase 1
4. Propose Phase 0 PR before writing meal-plan code
