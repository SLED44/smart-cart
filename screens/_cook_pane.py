"""
screens/_cook_pane.py
---------------------
Builds the embedded HTML/JS "cooking pane" used by the cook screen: two
**independently scrolling** columns (ingredients | instructions) so you never
scroll between them at the stove. Rendered via ``streamlit.components.v1.html``
in a fixed-height iframe — the iframe IS the "full-viewport frame" from the
design hand-off, and the two ``overflow-y:auto`` columns scroll inside it.

What lives in the browser (no Python round-trip needed):
    - check-off state per ingredient
    - the active step
    - step → ingredient highlighting
All three persist to ``localStorage["cookmode_v3"]`` keyed by recipe id, so
state survives Streamlit reruns. Feedback that needs persistence (rating,
"Made it", notes, never-again) stays as Streamlit widgets below the pane.

Public surface:
    build_cook_pane(recipe, scale, height) -> str   (full HTML doc for the iframe)
    PANE_HEIGHT                              -> int  (suggested iframe height)
"""

from __future__ import annotations

import html
import json

from sc_design import recipe_art

PANE_HEIGHT = 740  # iframe height; header + two columns that scroll inside it.

# Generic words too weak to anchor a step→ingredient highlight (mirrors the
# tokenizer in mealplan_cook so highlighting matches the rest of the app).
_HIGHLIGHT_STOP = {"oil", "salt", "water", "sugar", "pepper", "butter", "broth"}


def _step_uses_ingredient(step_text: str, name: str) -> bool:
    name = (name or "").lower().strip()
    if not name:
        return False
    text = step_text.lower()
    if name in text:
        return True
    head = name.split()[-1] if name.split() else ""
    return len(head) >= 4 and head not in _HIGHLIGHT_STOP and head in text


def _line_to_html(line: str) -> str:
    """Convert a format_ingredient_line() result to safe HTML, honoring its
    single ``**amount**`` bold marker."""
    if "**" in line:
        # Exactly one '**amount** rest' marker from format_ingredient_line.
        before, _, rest = line.partition("**")
        amount, _, tail = rest.partition("**")
        return (f"{html.escape(before)}<strong>{html.escape(amount)}</strong>"
                f"{html.escape(tail)}")
    return html.escape(line)


def build_cook_pane(recipe: dict, scale: float, height: int = PANE_HEIGHT) -> str:
    """Return a self-contained HTML document for the cooking pane iframe."""
    from screens import _recipe_view  # local import avoids a cycle at module load

    rid = recipe.get("id", "r")
    ings = recipe.get("ingredients") or []
    steps = recipe.get("instructions") or []

    # Group ingredients, preserving each one's original index for stable keys.
    groups: dict[str, list[tuple[int, dict]]] = {}
    for i, ing in enumerate(ings):
        groups.setdefault((ing.get("group") or "").strip(), []).append((i, ing))

    # Precompute the step → highlighted-ingredient-index map in Python (keeps
    # the tokenizer identical across the app; JS just toggles classes).
    highlight = {}
    for s_idx, step in enumerate(steps, start=1):
        text = step.get("text", "")
        hits = [i for i, ing in enumerate(ings)
                if _step_uses_ingredient(text, ing.get("name", ""))]
        if hits:
            highlight[str(s_idx)] = hits

    # ---- Ingredient column markup -----------------------------------------
    ing_rows = []
    for group_name, items in groups.items():
        if group_name:
            ing_rows.append(
                f'<div class="grp">{html.escape(group_name)}</div>'
            )
        for i, ing in items:
            line_html = _line_to_html(_recipe_view.format_ingredient_line(ing, scale))
            ing_rows.append(
                f'<div class="ing" data-idx="{i}" role="checkbox" tabindex="0" '
                f'aria-checked="false">'
                f'<span class="box"></span>'
                f'<span class="txt">{line_html}</span></div>'
            )
    ing_html = "\n".join(ing_rows)

    # ---- Step column markup ------------------------------------------------
    step_cards = []
    for idx, step in enumerate(steps, start=1):
        num = step.get("step_number", idx)
        text = html.escape(step.get("text", ""))
        step_cards.append(
            f'<div class="step" data-step="{idx}">'
            f'<span class="num">{html.escape(str(num))}</span>'
            f'<span class="stxt">{text}</span></div>'
        )
    steps_html = "\n".join(step_cards)

    n_ing = len(ings)
    n_steps = len(steps)
    hl_json = json.dumps(highlight)
    rid_js = json.dumps(rid)

    return _TEMPLATE.format(
        height=height,
        header_html=_build_header(recipe),
        ing_html=ing_html,
        steps_html=steps_html,
        n_ing=n_ing,
        n_steps=n_steps,
        hl_json=hl_json,
        rid_js=rid_js,
    )


def _build_header(recipe: dict) -> str:
    """In-iframe recipe header: art/photo tile, title + cuisine pill, meta
    chips. Living inside the iframe (rather than as Streamlit chrome above it)
    is what lets the cooking columns own the full frame height and scroll
    independently without the outer page moving."""
    title = html.escape(recipe.get("title", "(untitled)"))
    cuisines = recipe.get("cuisines") or []
    cuisine = cuisines[0] if cuisines else ""
    glyph = recipe.get("glyph", "🍽")

    if recipe.get("image_url"):
        tile = (f'<img class="tile" src="{html.escape(recipe["image_url"])}" '
                f'alt="" />')
    else:
        tile = f'<div class="tile">{recipe_art(glyph, cuisine, size=58)}</div>'

    pill = (f'<span class="cpill">{html.escape(cuisine.title())}</span>'
            if cuisine else "")

    chips = []
    if recipe.get("ready_in_minutes"):
        chips.append(f'⏱ {html.escape(str(recipe["ready_in_minutes"]))} min')
    if recipe.get("prep_minutes"):
        chips.append(f'🔪 {html.escape(str(recipe["prep_minutes"]))} min prep')
    if recipe.get("cook_minutes"):
        chips.append(f'🔥 {html.escape(str(recipe["cook_minutes"]))} min cook')
    if recipe.get("servings_original"):
        chips.append(f'🍽 serves {html.escape(str(recipe["servings_original"]))}')
    if recipe.get("equipment"):
        chips.append(f'🍳 {html.escape(", ".join(recipe["equipment"]))}')
    chip_html = "".join(f'<span class="chip">{c}</span>' for c in chips)

    return (
        f'<div class="topbar">{tile}'
        f'<div class="hmeta"><div class="htitle">{title}{pill}</div>'
        f'<div class="chips">{chip_html}</div></div></div>'
    )


# The iframe document. height/100% + overflow:hidden on html/body, body as a
# minmax(0,1fr) grid row → the two columns scroll independently (handoff note
# on iPad independent scroll). Tokens are the hand-off hex equivalents.
_TEMPLATE = """\
<!doctype html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    height: 100%; margin: 0; overflow: hidden;
    font-family: "Source Sans 3","Source Sans Pro",-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
    color: #212529; background: #f7faf8;
  }}
  /* Frame = header (auto) + columns (fill). overflow:hidden on the frame plus
     overscroll-behavior:contain on the scroll areas keeps the wheel inside a
     column instead of chaining to the outer Streamlit page. */
  .frame {{ height: {height}px; display: flex; flex-direction: column; padding: 2px; }}
  .topbar {{
    flex: 0 0 auto; display: flex; align-items: center; gap: 14px;
    padding: 4px 4px 14px;
  }}
  .topbar .tile {{
    width: 58px; height: 58px; flex-shrink: 0; border-radius: 12px;
    overflow: hidden; display: flex; align-items: center; justify-content: center;
  }}
  .topbar img.tile {{ object-fit: cover; }}
  .topbar .htitle {{ font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }}
  .topbar .cpill {{
    display: inline-block; vertical-align: middle; margin-left: 10px;
    font-size: 12px; font-weight: 600; color: #7a3fb0; background: #ece4f5;
    border-radius: 999px; padding: 2px 10px;
  }}
  .topbar .chips {{ margin-top: 5px; display: flex; flex-wrap: wrap; gap: 6px; }}
  .topbar .chip {{
    font-size: 12.5px; color: #495057; background: #ffffff;
    border: 1px solid #dee2e6; border-radius: 999px; padding: 3px 10px;
  }}
  .wrap {{
    flex: 1 1 auto; min-height: 0; display: grid;
    grid-template-columns: minmax(320px, 380px) 1fr; gap: 14px;
  }}
  .col {{
    border: 1px solid #dee2e6; border-radius: 12px; background: #ffffff;
    display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}
  .col > .head {{ padding: 14px 16px 10px; border-bottom: 1px solid #e9ecef; }}
  .col > .scroll {{
    overflow-y: auto; overscroll-behavior: contain; padding: 10px 14px 18px;
  }}
  .head .title {{ font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }}
  .head .cap {{ font-size: 12.5px; color: #6c757d; margin-top: 2px; }}
  .grp {{
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; color: #6c757d; margin: 14px 4px 6px;
  }}
  .grp:first-child {{ margin-top: 4px; }}
  .ing {{
    display: flex; align-items: flex-start; gap: 10px;
    padding: 9px 10px; border-radius: 8px; cursor: pointer;
    border: 1px solid transparent; transition: background .1s, border-color .1s, opacity .1s;
    font-size: 14.5px; line-height: 1.35;
  }}
  .ing:hover {{ background: #f8f9fa; }}
  .ing .box {{
    flex-shrink: 0; width: 21px; height: 21px; border-radius: 999px;
    border: 2px solid #adb5bd; margin-top: 1px; position: relative;
    transition: background .1s, border-color .1s;
  }}
  .ing.checked {{ opacity: 0.5; }}
  .ing.checked .txt {{ text-decoration: line-through; }}
  .ing.checked .box {{ background: #2e9e54; border-color: #2e9e54; }}
  .ing.checked .box::after {{
    content: "✓"; color: #fff; font-size: 13px; font-weight: 700;
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  }}
  .ing.hl {{ background: #eef8f1; border-color: #a9e0b8; }}
  .step {{
    display: flex; gap: 12px; align-items: flex-start;
    padding: 14px; margin-bottom: 10px; border-radius: 10px;
    border: 1px solid #dee2e6; background: #ffffff; cursor: pointer;
    transition: border-color .1s, background .1s, box-shadow .1s;
  }}
  .step:hover {{ background: #f8f9fa; }}
  .step .num {{
    flex-shrink: 0; width: 30px; height: 30px; border-radius: 999px;
    background: #f8f9fa; color: #268048; border: 1px solid #e9ecef;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 14px;
  }}
  .step .stxt {{ font-size: 16.5px; line-height: 1.5; padding-top: 3px; }}
  .step.active {{
    border-color: #2e9e54; background: #eef8f1;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }}
  .step.active .num {{ background: #2e9e54; color: #fff; border-color: #2e9e54; }}
  .scroll::-webkit-scrollbar {{ width: 10px; }}
  .scroll::-webkit-scrollbar-thumb {{ background: #dee2e6; border-radius: 999px; }}
  /* Phone / narrow: stack the columns and let the whole frame scroll as one
     (independent column scroll is intentionally dropped below ~720px, per the
     hand-off responsive notes). The media query keys off the iframe width. */
  @media (max-width: 720px) {{
    .topbar .htitle {{ font-size: 19px; }}
    .frame {{ overflow-y: auto; overscroll-behavior: contain; }}
    .wrap {{ grid-template-columns: 1fr; flex: 0 0 auto; }}
    .col {{ grid-template-rows: auto auto; }}
    .col > .scroll {{ overflow-y: visible; }}
    .col + .col {{ margin-top: 14px; }}
  }}
</style></head>
<body>
  <div class="frame">
  {header_html}
  <div class="wrap">
    <div class="col" id="ingcol">
      <div class="head">
        <div class="title">🧺 Ingredients · <span id="ingcount">0</span> of {n_ing} in</div>
        <div class="cap" id="ingcap">Tap to check off as you add them.</div>
      </div>
      <div class="scroll">{ing_html}</div>
    </div>
    <div class="col" id="stepcol">
      <div class="head">
        <div class="title" id="stephead">Instructions · {n_steps} steps</div>
        <div class="cap">Tap a step to focus it — its ingredients light up on the left.</div>
      </div>
      <div class="scroll" id="stepscroll">{steps_html}</div>
    </div>
  </div>
  </div>
<script>
(function() {{
  var RID = {rid_js};
  var HL = {hl_json};
  var NSTEPS = {n_steps};
  var KEY = "cookmode_v3";

  function load() {{
    try {{ return JSON.parse(localStorage.getItem(KEY) || "{{}}"); }}
    catch (e) {{ return {{}}; }}
  }}
  function save(all) {{
    try {{ localStorage.setItem(KEY, JSON.stringify(all)); }} catch (e) {{}}
  }}
  var all = load();
  var st = all[RID] || {{ checked: {{}}, active: 0 }};
  all[RID] = st;

  var ings = Array.prototype.slice.call(document.querySelectorAll(".ing"));
  var steps = Array.prototype.slice.call(document.querySelectorAll(".step"));
  var countEl = document.getElementById("ingcount");
  var capEl = document.getElementById("ingcap");
  var headEl = document.getElementById("stephead");

  function updateCount() {{
    var c = 0;
    ings.forEach(function(el) {{ if (el.classList.contains("checked")) c++; }});
    countEl.textContent = c;
  }}

  function applyHighlight() {{
    var hits = HL[String(st.active)] || [];
    var set = {{}};
    hits.forEach(function(i) {{ set[i] = true; }});
    ings.forEach(function(el) {{
      el.classList.toggle("hl", !!set[el.getAttribute("data-idx")]);
    }});
    capEl.textContent = st.active
      ? ("Highlighted ingredients are used in step " + st.active + ".")
      : "Tap to check off as you add them.";
    headEl.textContent = st.active
      ? ("Step " + st.active + " of " + NSTEPS)
      : ("Instructions · " + NSTEPS + " steps");
  }}

  function setActive(n, scroll) {{
    st.active = (st.active === n) ? 0 : n;
    steps.forEach(function(el) {{
      el.classList.toggle("active", parseInt(el.getAttribute("data-step"),10) === st.active);
    }});
    applyHighlight();
    save(all);
    if (scroll && st.active) {{
      var t = steps[st.active - 1];
      if (t) t.scrollIntoView({{ behavior: "smooth", block: "center" }});
    }}
  }}

  function toggleIng(el) {{
    var idx = el.getAttribute("data-idx");
    var on = !el.classList.contains("checked");
    el.classList.toggle("checked", on);
    el.setAttribute("aria-checked", on ? "true" : "false");
    if (on) st.checked[idx] = true; else delete st.checked[idx];
    save(all);
    updateCount();
  }}

  // Restore persisted state.
  ings.forEach(function(el) {{
    if (st.checked[el.getAttribute("data-idx")]) {{
      el.classList.add("checked");
      el.setAttribute("aria-checked", "true");
    }}
    el.addEventListener("click", function() {{ toggleIng(el); }});
    el.addEventListener("keydown", function(e) {{
      if (e.key === " " || e.key === "Enter") {{ e.preventDefault(); toggleIng(el); }}
    }});
  }});
  steps.forEach(function(el) {{
    el.addEventListener("click", function() {{
      setActive(parseInt(el.getAttribute("data-step"), 10), true);
    }});
  }});

  updateCount();
  if (st.active) {{
    steps.forEach(function(el) {{
      el.classList.toggle("active", parseInt(el.getAttribute("data-step"),10) === st.active);
    }});
    applyHighlight();
  }}
}})();
</script>
</body></html>
"""
