"""
Paste-recipe screen — accept a JSON blob (typically produced by Claude.ai
chat) and save it as a library recipe.

In-app, never calls Anthropic. The expected schema mirrors PRD §7.2 and
is rendered at the top of the screen for easy copy into a Claude.ai
prompt.
"""

import json

import streamlit as st

from mealplan import library
from mealplan.rules import load_rules, save_rules

from screens._shared import go

DEFAULT_FAV_CADENCE = [4, 6]

# ---------------------------------------------------------------------------
# Schema + prompt template (shown to user for copy)
# ---------------------------------------------------------------------------

SCHEMA_BLOCK = '''{
  "title":             "Required, string",
  "source_url":        "Optional, the original recipe URL or empty string",
  "image_url":         "Optional, image URL (CDN) or empty string",
  "servings_original": 4,
  "ready_in_minutes":  30,
  "prep_minutes":      10,
  "cook_minutes":      20,
  "cuisines":          ["american"],
  "diet_tags":         [],
  "dish_types":        ["main course"],
  "equipment":         ["air_fryer", "sheet_pan"],
  "proteins":          ["beef"],
  "carbs":             ["bread"],
  "ingredients": [
    {
      "name":          "ground beef (80/20)",
      "amount":        1.5,
      "unit":          "lbs",
      "aisle":         "Meat",
      "original_text": "1.5 lbs 80/20 ground beef"
    }
  ],
  "instructions": [
    { "step_number": 1, "text": "Preheat air fryer to 425°F for 5 min." }
  ]
}'''

# Allowed values surfaced to the user so the prompt template is self-contained.
_PROTEINS = ("beef", "pork", "chicken", "turkey", "fish", "lamb", "shrimp", "plant")
_CARBS = ("rice", "pasta", "bread", "grain", "potato", "salad")

CLAUDE_PROMPT_TEMPLATE = '''Convert this recipe to the SmartCart Meal Planner JSON schema.

**Household preferences:**
- Size: 4
- Spice tolerance: mild (no spicy/hot/fire/ghost/habanero variants)
- Default appliance: air fryer (rewrite "bake at 400°F for 20 min" as "air fry at 400°F for ~16 min" when adapting)
- Sauce philosophy: store-bought preferred (don't fabricate from-scratch sauce recipes)

**Schema (output ONE JSON object, no markdown fence, no commentary):**
{SCHEMA}

**Required field rules:**
- `title` — short, no brand names
- `servings_original` — the recipe's original yield
- `ready_in_minutes` — total prep + cook
- `cuisines` — lowercase. Pick from: american, italian, mexican, japanese,
  korean, vietnamese, thai, chinese, mediterranean, greek, middle_eastern,
  moroccan, indian (or add a new one if needed)
- `proteins` — pick from: {PROTEINS}. Use "plant" for tofu/legumes/tempeh.
- `carbs` — pick from: {CARBS}
- `equipment` — lowercase_with_underscores (e.g. air_fryer, sheet_pan,
  dutch_oven, instant_pot, skillet, stovetop, oven, grill, slow_cooker)
- `ingredients[].amount` — number (decimal OK). `unit` — "lb", "oz", "g",
  "cup", "tbsp", "tsp", "ml", "count", "" (for things like "1 onion").
  `aisle` — Spoonacular-style: Meat, Seafood, Produce, Dairy, Pantry,
  Frozen, Bakery, Beverages, Spices, Canned and Jarred, Baking.
- `instructions[].step_number` — 1-indexed integer, sequential.

**Recipe to convert:**
[PASTE YOUR RECIPE TEXT OR URL HERE]
'''


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def render():
    st.title("📝 Paste a recipe")
    st.caption(
        "Drop in JSON from a Claude.ai chat. Use this for canonical favorites "
        "Spoonacular can't find, family recipes, or NYT/blog recipes you want "
        "to keep."
    )

    col_back, _ = st.columns([1, 5])
    with col_back:
        if st.button("← Meal-planner home", key="paste_back"):
            _clear_session()
            go("mealplan_home")

    _render_help()

    st.divider()
    st.subheader("Paste JSON below")

    raw = st.text_area(
        "JSON",
        value=st.session_state.get("paste_recipe_raw", ""),
        height=300,
        key="paste_recipe_input",
        label_visibility="collapsed",
        placeholder='{"title": "My Smash Burgers", "servings_original": 4, ...}',
    )
    st.session_state.paste_recipe_raw = raw

    col_parse, col_clear = st.columns([1, 1])
    with col_parse:
        if st.button("✓ Validate & preview", type="primary", key="paste_validate",
                     use_container_width=True):
            _validate_and_stash(raw)
    with col_clear:
        if st.button("Clear", key="paste_clear", use_container_width=True):
            _clear_session()
            st.rerun()

    parsed = st.session_state.get("paste_recipe_parsed")
    if parsed is None:
        return

    st.divider()
    _render_preview_and_save(parsed)


# ---------------------------------------------------------------------------
# Help section
# ---------------------------------------------------------------------------

def _render_help():
    with st.expander("📋 JSON schema (copy this into Claude.ai)", expanded=False):
        st.code(SCHEMA_BLOCK, language="json")
        st.caption("Save this as a snippet — every paste-recipe import uses the same shape.")

    with st.expander("💬 Full Claude.ai prompt template (copy + paste recipe at the bottom)"):
        rendered = CLAUDE_PROMPT_TEMPLATE.format(
            SCHEMA=SCHEMA_BLOCK,
            PROTEINS=", ".join(_PROTEINS),
            CARBS=", ".join(_CARBS),
        )
        st.code(rendered, language="markdown")
        st.caption("Workflow: paste this whole prompt into Claude.ai, append your recipe "
                   "text or URL at the bottom, copy the JSON response, paste below.")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_and_stash(raw: str):
    if not raw.strip():
        st.error("Paste some JSON first.")
        return
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        st.error(f"Couldn't parse JSON: {e.msg} (line {e.lineno}, col {e.colno})")
        return

    errs = _validate_recipe(parsed)
    if errs:
        st.error("Recipe fails validation — fix and retry:")
        for e in errs:
            st.write(f"• {e}")
        # Still stash so the preview can show what was parsed
        st.session_state["paste_recipe_parsed"] = parsed
        st.session_state["paste_recipe_errors"] = errs
        return

    # Normalise defaults so library.save gets a clean record.
    parsed.setdefault("source", "claude_chat")
    parsed.setdefault("dish_types", ["main course"])
    parsed.setdefault("diet_tags", [])
    parsed.setdefault("equipment", [])
    parsed.setdefault("source_url", "")
    parsed.setdefault("image_url", "")

    st.session_state["paste_recipe_parsed"] = parsed
    st.session_state["paste_recipe_errors"] = []
    st.success("Recipe looks valid. Review the preview below, then save.")


def _validate_recipe(r: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(r, dict):
        return ["Top-level value must be a JSON object."]
    if not r.get("title") or not isinstance(r["title"], str):
        errs.append("`title` is required (non-empty string).")
    if not isinstance(r.get("servings_original"), int) or r["servings_original"] < 1:
        errs.append("`servings_original` must be a positive integer.")
    if not isinstance(r.get("ready_in_minutes"), int) or r["ready_in_minutes"] < 0:
        errs.append("`ready_in_minutes` must be a non-negative integer.")
    for k in ("cuisines", "proteins", "carbs", "dish_types", "diet_tags", "equipment"):
        v = r.get(k)
        if v is not None and (not isinstance(v, list) or
                              not all(isinstance(x, str) for x in v)):
            errs.append(f"`{k}` must be an array of strings.")
    for p in (r.get("proteins") or []):
        if p not in _PROTEINS:
            errs.append(f"`proteins` contains unknown value {p!r}; must be one of {_PROTEINS}.")
    for c in (r.get("carbs") or []):
        if c not in _CARBS:
            errs.append(f"`carbs` contains unknown value {c!r}; must be one of {_CARBS}.")

    ings = r.get("ingredients")
    if not isinstance(ings, list) or not ings:
        errs.append("`ingredients` must be a non-empty array.")
    else:
        for i, ing in enumerate(ings):
            if not isinstance(ing, dict):
                errs.append(f"`ingredients[{i}]` must be an object.")
                continue
            if not ing.get("name"):
                errs.append(f"`ingredients[{i}].name` is required.")
            amt = ing.get("amount")
            if not isinstance(amt, (int, float)) or amt < 0:
                errs.append(f"`ingredients[{i}].amount` must be a non-negative number.")
            if not isinstance(ing.get("unit", ""), str):
                errs.append(f"`ingredients[{i}].unit` must be a string (use \"\" for none).")
            if not isinstance(ing.get("aisle", ""), str):
                errs.append(f"`ingredients[{i}].aisle` must be a string.")

    steps = r.get("instructions")
    if not isinstance(steps, list) or not steps:
        errs.append("`instructions` must be a non-empty array.")
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                errs.append(f"`instructions[{i}]` must be an object.")
                continue
            if not isinstance(s.get("step_number"), int):
                errs.append(f"`instructions[{i}].step_number` must be an integer.")
            if not s.get("text"):
                errs.append(f"`instructions[{i}].text` is required.")
    return errs


# ---------------------------------------------------------------------------
# Preview + save
# ---------------------------------------------------------------------------

def _render_preview_and_save(parsed: dict):
    st.subheader("Preview")
    errs = st.session_state.get("paste_recipe_errors") or []
    if errs:
        st.error(f"{len(errs)} validation error(s) — fix the JSON and re-validate.")

    col_img, col_body = st.columns([1, 3])
    with col_img:
        if parsed.get("image_url"):
            st.image(parsed["image_url"], width=200)
    with col_body:
        st.markdown(f"### {parsed.get('title','(untitled)')}")
        meta = []
        if parsed.get("cuisines"):
            meta.append(", ".join(parsed["cuisines"]))
        if parsed.get("proteins"):
            meta.append("· " + "/".join(parsed["proteins"]))
        if parsed.get("ready_in_minutes"):
            meta.append(f"· {parsed['ready_in_minutes']} min")
        if parsed.get("servings_original"):
            meta.append(f"· serves {parsed['servings_original']}")
        if meta:
            st.caption(" ".join(meta))
        if parsed.get("equipment"):
            st.caption(f"Equipment: {', '.join(parsed['equipment'])}")
        if parsed.get("source_url"):
            st.markdown(f"[Source ↗]({parsed['source_url']})")

    with st.expander(f"Ingredients ({len(parsed.get('ingredients') or [])})"):
        for ing in parsed.get("ingredients") or []:
            unit = f" {ing.get('unit')}" if ing.get("unit") else ""
            aisle = f"  _(_{ing.get('aisle','')}_)_" if ing.get("aisle") else ""
            st.write(f"• {ing.get('amount','?')}{unit} {ing.get('name','?')}{aisle}")

    with st.expander(f"Instructions ({len(parsed.get('instructions') or [])} steps)"):
        for step in parsed.get("instructions") or []:
            st.write(f"**{step.get('step_number','?')}.** {step.get('text','')}")

    if errs:
        return  # Don't surface save controls until errors are fixed.

    st.divider()
    st.subheader("Save options")

    # ID picker — let user overwrite an existing recipe (e.g. replacing a
    # Spoonacular bootstrap miss with the canonical paste-import).
    existing = library.get_all() or {}
    existing_ids = sorted(existing.keys())

    suggested = parsed.get("id") or f"lib_{library.slugify(parsed['title'])}"
    mode = st.radio(
        "Save as",
        ["new recipe (auto-id)", "overwrite an existing recipe"],
        index=0, horizontal=True, key="paste_save_mode",
    )

    if mode.startswith("new"):
        custom_id = st.text_input(
            "Recipe id",
            value=suggested,
            help="Defaults to lib_<slug>. Pick something stable — favorites + "
                 "exclusions reference recipes by id.",
            key="paste_save_id_new",
        )
    else:
        if not existing_ids:
            st.info("No existing recipes to overwrite. Switch to 'new recipe'.")
            return
        custom_id = st.selectbox(
            "Overwrite which recipe?", existing_ids,
            format_func=lambda rid: f"{rid} — {existing[rid].get('title','(untitled)')}",
            key="paste_save_id_overwrite",
        )
        st.caption(
            "🛡 Protected fields on the existing recipe (`user_notes`, "
            "`times_cooked`, `last_cooked_at`, `status`) are preserved by "
            "library.save's merge logic."
        )

    mark_fav = st.checkbox(
        "Mark as favorite (adds to rules with cadence [4, 6])",
        value=False, key="paste_save_fav",
    )

    src = st.selectbox(
        "Source tag",
        ["claude_chat", "user_manual"],
        index=0, key="paste_save_source",
        help="claude_chat = converted via Claude.ai. user_manual = typed up by hand.",
    )

    if st.button("💾 Save to library", type="primary",
                 use_container_width=True, key="paste_save_btn"):
        recipe = dict(parsed)
        recipe["id"] = custom_id
        recipe["source"] = src
        if mark_fav:
            recipe["status"] = "favorite"
        rid = library.save(recipe)
        st.success(f"Saved as `{rid}`.")

        if mark_fav:
            rules = load_rules()
            existing_fav = {f.get("recipe_id") for f in (rules.get("favorites") or [])}
            if rid not in existing_fav:
                rules.setdefault("favorites", []).append({
                    "recipe_id":      rid,
                    "cadence_weeks":  list(DEFAULT_FAV_CADENCE),
                    "last_used_week": None,
                })
                save_rules(rules)
                st.caption(f"⭐ Added to favorites (cadence {DEFAULT_FAV_CADENCE}).")
            else:
                st.caption("Already in favorites — left as-is.")

        # Clear the form so the next paste starts fresh.
        _clear_session()


def _clear_session():
    for k in ("paste_recipe_raw", "paste_recipe_parsed", "paste_recipe_errors"):
        st.session_state.pop(k, None)
