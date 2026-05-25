"""
Paste-and-parse importer for the meal-plan engine state.

Use case: user has a rules doc (likely in Notion / Google Docs) with a
"Current State" section. Paste it here, regex-extract values, show what
was captured, let user fix anything wrong, then apply.

Touches only:
    meal_plan_rules.state.current_week
    meal_plan_rules.state.shrimp_counter
    meal_plan_rules.favorites[i].last_used_week
    pending_lineup (if a lineup section was found)

Never overwrites the rest of the rules blob. No LLM call (PRD §15.3).
"""

import re

import streamlit as st

from mealplan import library
from mealplan.rules import load_rules, save_rules
from supabase_kv import kv_put

from screens._shared import go

KEY_PENDING_LINEUP = "pending_lineup"
_DRAFT_KEY = "mealplan_state_import_draft"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render():
    st.title("📥 Import meal-plan state")
    st.caption("Paste your 'Current State' section from your rules doc. We'll "
               "regex out the values, you fix anything wrong, then apply.")

    col_back, _ = st.columns([1, 5])
    with col_back:
        if st.button("← Home", key="state_import_back"):
            _discard_draft()
            go("home")

    st.divider()
    text = st.text_area(
        "Paste the Current State section",
        value=st.session_state.get("state_import_raw", ""),
        height=240,
        placeholder=(
            "Example:\n"
            "Current week: 6\n"
            "Shrimp counter: 2\n"
            "Last used (favorites):\n"
            "  - Smash Burgers: week 1\n"
            "  - Chicken Katsu: week 4\n"
            "Pending lineup:\n"
            "  - Miso Glazed Salmon\n"
            "  - Lamb Meatballs\n"
        ),
        key="state_import_input",
    )
    st.session_state.state_import_raw = text

    col_parse, col_clear = st.columns(2)
    with col_parse:
        if st.button("Parse", type="primary", key="state_import_parse",
                     use_container_width=True):
            st.session_state[_DRAFT_KEY] = _parse(text)
            st.rerun()
    with col_clear:
        if st.button("Clear", key="state_import_clear", use_container_width=True):
            st.session_state.state_import_raw = ""
            _discard_draft()
            st.rerun()

    draft = st.session_state.get(_DRAFT_KEY)
    if not draft:
        return

    st.divider()
    st.subheader("What we parsed")
    st.caption("Tweak anything wrong before applying.")

    draft["current_week"] = int(st.number_input(
        "Current week", min_value=1, max_value=520,
        value=int(draft.get("current_week") or 1), step=1,
        key="state_import_cw"))

    draft["shrimp_counter"] = int(st.number_input(
        "Shrimp counter (weeks since last shrimp dish)",
        min_value=0, max_value=52,
        value=int(draft.get("shrimp_counter") or 0), step=1,
        key="state_import_sc"))

    # Favorites — show every match candidate, let user accept/edit each one.
    if draft.get("favorite_last_used"):
        st.markdown("**Favorites last-used**")
        fav_options = _favorite_id_options()
        st.caption("Each row is a 'Last used Week N' match we found. Confirm the "
                   "recipe id (match against existing favorites if possible).")
        for i, entry in enumerate(draft["favorite_last_used"]):
            col_text, col_rid, col_wk, col_keep = st.columns([3, 3, 1, 1])
            with col_text:
                st.write(f"_{entry['raw_phrase'][:60]}_")
            with col_rid:
                entry["recipe_id"] = _favorite_picker(
                    entry.get("recipe_id", ""), fav_options,
                    key=f"state_fav_rid_{i}")
            with col_wk:
                entry["last_used_week"] = int(st.number_input(
                    "week", min_value=0, max_value=520,
                    value=int(entry.get("last_used_week") or 0), step=1,
                    key=f"state_fav_wk_{i}", label_visibility="collapsed"))
            with col_keep:
                entry["apply"] = st.checkbox(
                    "apply", value=entry.get("apply", True),
                    key=f"state_fav_apply_{i}")

    if draft.get("pending_lineup_titles"):
        st.markdown("**Pending lineup (raw titles)**")
        for title in draft["pending_lineup_titles"]:
            st.write(f"• {title}")
        st.caption("These titles will be saved to `pending_lineup` as-is. "
                   "The propose screen (Phase 6) reconciles them against the library.")

    if draft.get("warnings"):
        with st.expander(f"⚠ {len(draft['warnings'])} parse warning(s)"):
            for w in draft["warnings"]:
                st.write(f"• {w}")

    st.divider()
    if st.button("✓ Apply to rules", type="primary",
                 key="state_import_apply", use_container_width=True):
        _apply(draft)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Lines like "Smash Burgers — Last used Week 4" or "Last used: Week 1 (Smash Burgers)".
_FAV_LU_RE = re.compile(
    r"""(?ix)
    (?:                                 # Either: <name> last used week N
        (?P<name1>[A-Za-z][A-Za-z0-9\ \-'&]+?)
        \s*[\-—:]?\s*
        last\s+used.*?\b
        week\s+(?P<wk1>\d+)
    )
    |
    (?:                                 # Or: last used week N (<name>)
        last\s+used.*?\bweek\s+(?P<wk2>\d+)
        \s*[\(\[]?\s*(?P<name2>[A-Za-z][A-Za-z0-9\ \-'&]+?)\s*[\)\]]?
        (?=$|\n)
    )
    """,
)


def _parse(text: str) -> dict:
    out: dict = {"warnings": []}

    # current_week — "current week: 6" or "week 6" (loose)
    m = re.search(r"(?i)current\s+week\s*[:=]?\s*(\d+)", text)
    if not m:
        m = re.search(r"(?im)^\s*week\s+(\d+)\b", text)
    if m:
        out["current_week"] = int(m.group(1))
    else:
        out["warnings"].append("couldn't find current_week — defaulted to 1")
        out["current_week"] = 1

    # shrimp_counter — "shrimp counter: 2" or "shrimp: 2"
    m = re.search(r"(?i)shrimp(?:\s+counter)?\s*[:=]?\s*(\d+)", text)
    if m:
        out["shrimp_counter"] = int(m.group(1))
    else:
        out["shrimp_counter"] = 0

    # Favorites last-used
    fav_hits = []
    seen_names = set()
    fav_options = _favorite_id_options()
    for m in _FAV_LU_RE.finditer(text):
        name = (m.group("name1") or m.group("name2") or "").strip()
        wk_raw = m.group("wk1") or m.group("wk2")
        if not name or not wk_raw:
            continue
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        rid = _fuzzy_match_favorite(name, fav_options)
        fav_hits.append({
            "raw_phrase":      name + (f" — week {wk_raw}"),
            "recipe_id":       rid,
            "last_used_week":  int(wk_raw),
            "apply":           bool(rid),
        })
    out["favorite_last_used"] = fav_hits
    if not fav_hits:
        out["warnings"].append("no favorites last-used entries parsed")

    # Pending lineup — pull the lines under "Pending lineup:" until blank line.
    lineup_lines = _extract_section(
        text, header_pattern=r"(?i)^\s*pending\s+lineup\b.*$",
    )
    out["pending_lineup_titles"] = [
        re.sub(r"^[\s•\-\*\d\.\)]+", "", line).strip()
        for line in lineup_lines if line.strip()
    ]

    return out


def _extract_section(text: str, *, header_pattern: str) -> list[str]:
    """Return the lines following a heading until the next blank/heading line."""
    lines = text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        if not in_section:
            if re.match(header_pattern, line):
                in_section = True
            continue
        if not line.strip():
            break
        out.append(line)
    return out


def _favorite_id_options() -> list[tuple[str, str]]:
    """Build [(recipe_id, title)] for matching parsed names against."""
    out: list[tuple[str, str]] = []
    rules = load_rules()
    fav_ids = [f.get("recipe_id") for f in (rules.get("favorites") or []) if f.get("recipe_id")]
    for rid in fav_ids:
        recipe = library.get(rid)
        title = recipe.get("title") if recipe else rid
        out.append((rid, title or rid))
    # Also offer the rest of the library, since the user may want to map a
    # parsed phrase to a not-yet-favorite recipe.
    for rid, recipe in (library.get_all() or {}).items():
        if rid not in {x[0] for x in out}:
            out.append((rid, recipe.get("title") or rid))
    return out


def _fuzzy_match_favorite(name: str, options: list[tuple[str, str]]) -> str:
    """Greedy substring + slug match. Returns recipe_id or empty string."""
    n = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    n_words = set(n.split())
    best_id, best_score = "", 0.0
    for rid, title in options:
        candidates = [rid, title or ""]
        for c in candidates:
            c_norm = re.sub(r"[^a-z0-9]+", " ", c.lower()).strip()
            if not c_norm:
                continue
            c_words = set(c_norm.split())
            overlap = len(n_words & c_words)
            if overlap == 0:
                continue
            score = overlap / max(1, len(n_words | c_words))
            if score > best_score:
                best_score, best_id = score, rid
    return best_id if best_score >= 0.5 else ""


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _apply(draft: dict):
    rules = load_rules()
    state = rules.setdefault("state", {})
    state["current_week"] = int(draft.get("current_week") or 1)
    state["shrimp_counter"] = int(draft.get("shrimp_counter") or 0)

    applied_favs = 0
    fav_index = {f.get("recipe_id"): f for f in (rules.get("favorites") or [])}
    for entry in draft.get("favorite_last_used") or []:
        if not entry.get("apply"):
            continue
        rid = entry.get("recipe_id")
        if not rid:
            continue
        if rid in fav_index:
            fav_index[rid]["last_used_week"] = int(entry["last_used_week"])
            applied_favs += 1
        else:
            # Not a favorite yet — add it as one with a default cadence and the
            # parsed last-used.
            new_fav = {
                "recipe_id":      rid,
                "cadence_weeks":  [4, 6],
                "last_used_week": int(entry["last_used_week"]),
            }
            rules.setdefault("favorites", []).append(new_fav)
            applied_favs += 1

    try:
        save_rules(rules)
    except Exception as e:
        st.error(f"Saving rules failed: {e}")
        return

    if draft.get("pending_lineup_titles"):
        kv_put(KEY_PENDING_LINEUP, {
            "titles":       list(draft["pending_lineup_titles"]),
            "source":       "state_import",
            "confirmed_at": None,
        })

    st.success(
        f"Applied: current_week={state['current_week']}, "
        f"shrimp_counter={state['shrimp_counter']}, "
        f"{applied_favs} favorite(s) updated"
        + (f", {len(draft['pending_lineup_titles'])} pending lineup titles stored"
           if draft.get("pending_lineup_titles") else "")
        + "."
    )


def _favorite_picker(current: str, options: list[tuple[str, str]], *, key: str) -> str:
    ids = [rid for rid, _ in options]
    labels = [lab for _, lab in options]
    sentinel = "__custom__"
    all_options = ids + [sentinel]
    all_display = labels + ["✏ Type a custom id…"]

    if current in ids:
        idx = ids.index(current)
    else:
        idx = len(all_options) - 1

    chosen = st.selectbox(
        "recipe", all_options, index=idx,
        format_func=lambda v: all_display[all_options.index(v)] if v in all_options else v,
        key=key, label_visibility="collapsed")

    if chosen == sentinel:
        return st.text_input(
            "id", value=current if current not in ids else "",
            placeholder="lib_smash_burgers",
            key=f"{key}_custom", label_visibility="collapsed")
    return chosen


def _discard_draft():
    st.session_state.pop(_DRAFT_KEY, None)
