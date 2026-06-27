"""
SmartCart design-system helpers.

Drop this file into the smart-cart repo root alongside main.py, then:

    from sc_design import (
        match_badge, stat_card, savings_card,
        product_card, matching_row, progress_section,
    )

These return HTML strings ready for `st.markdown(..., unsafe_allow_html=True)`.

Why HTML strings (not st.* widgets)?
  Streamlit's native `st.metric`, `st.columns`, etc. give you no pixel
  control. To get the design-system look (the savings hero, the 140 px
  product image, the pastel stat tiles) we have to render the chrome
  ourselves. The CSS classes here are defined in production/style.css.
"""

from __future__ import annotations

from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Match badge — direct upgrade of the existing _badge() helper in main.py.
# Keep the existing call sites; this just rebrands the pill.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Color palette — literal oklch() values so we don't depend on :root CSS
# variables (Streamlit's DOM doesn't inherit them reliably). Keep in sync
# with the tokens in style.css.
# ---------------------------------------------------------------------------

PALETTE = {
    "green_100":  "oklch(93% 0.07 150)",
    "green_600":  "oklch(63% 0.18 148)",
    "green_700":  "oklch(54% 0.17 148)",
    "green_900":  "oklch(34% 0.10 148)",
    "amber_50":   "oklch(97% 0.03 78)",
    "amber_300":  "oklch(85% 0.15 70)",
    "amber_700":  "oklch(58% 0.16 50)",
    "sale_bg":    "oklch(94% 0.07 70)",
    "sale_fg":    "oklch(45% 0.13 50)",
    "oos_bg":     "oklch(94% 0.06 22)",
    "oos_fg":     "oklch(45% 0.16 22)",
    "preferred_bg": "oklch(94% 0.07 148)",
    "preferred_fg": "oklch(38% 0.13 148)",
    "best_bg":    "oklch(94% 0.05 220)",
    "best_fg":    "oklch(42% 0.13 232)",
    "needspick_bg": "oklch(94% 0.06 305)",
    "needspick_fg": "oklch(42% 0.16 305)",
    "notfound_bg": "oklch(94% 0.005 80)",
    "notfound_fg": "oklch(42% 0.005 80)",
    "fg":         "#212529",
    "fg_muted":   "#6c757d",
    "fg_subtle":  "#adb5bd",
    "border":     "#dee2e6",
    "border_soft": "#e9ecef",
    "bg_soft":    "#f8f9fa",
    "surface":    "#ffffff",
}

P = PALETTE  # short alias used inside f-strings

_BADGE_STYLES = {
    "Preferred Match":  (P["preferred_bg"], P["preferred_fg"]),
    "Preferred OOS":    (P["oos_bg"],       P["oos_fg"]),
    "Best Match":       (P["best_bg"],      P["best_fg"]),
    "Needs Your Pick":  (P["needspick_bg"], P["needspick_fg"]),
    "Not Found":        (P["notfound_bg"],  P["notfound_fg"]),
    "On Sale Alt":      (P["sale_bg"],      P["sale_fg"]),
}


def match_badge(match_type: str) -> str:
    bg, fg = _BADGE_STYLES.get(match_type, (P["best_bg"], P["best_fg"]))
    return (
        f'<span style="display:inline-block; padding:4px 12px; '
        f'border-radius:999px; font-size:0.78em; font-weight:600; '
        f'line-height:1.45; letter-spacing:0.01em; '
        f'background:{bg}; color:{fg};">{match_type}</span>'
    )


# ---------------------------------------------------------------------------
# Reason chips — small pastel pills shown under a recipe title on the
# propose/swap cards ("🆕 New to your rotation", "Adds Greek to the week").
# tone: "neutral" (default), "green" (favorite/loved), "amber" (rating).
# ---------------------------------------------------------------------------

def reason_chips(reasons: list[tuple[str, str]] | list[str]) -> str:
    """Render a row of reason chips. Each item is either a plain string
    (neutral) or a (text, tone) tuple. Render with st.html()."""
    tones = {
        "neutral": (P["bg_soft"],    P["fg"],        P["border_soft"]),
        "green":   ("oklch(97% 0.03 150)", P["green_900"], "oklch(87% 0.11 150)"),
        "amber":   (P["amber_50"],   "#b58100",      P["amber_300"]),
        "sky":     ("#dceaf5",       "#2f6f9e",      "oklch(85% 0.08 220)"),
    }
    pills = []
    for item in reasons:
        text, tone = item if isinstance(item, tuple) else (item, "neutral")
        bg, fg, bd = tones.get(tone, tones["neutral"])
        pills.append(
            f'<span style="display:inline-block; font-size:12px; font-weight:600; '
            f'color:{fg}; background:{bg}; border:1px solid {bd}; '
            f'border-radius:999px; padding:3px 10px; margin:0 6px 6px 0; '
            f'white-space:nowrap;">{text}</span>'
        )
    return f'<div style="margin:6px 0 2px; line-height:1.9;">{"".join(pills)}</div>'


# ---------------------------------------------------------------------------
# Plan banner — the home-screen pending (amber) / confirmed (green) cards.
# Renders the colored card with a heading, subtext, and meal-title chips; the
# action buttons (Resume/Discard, Open/Plan-new) stay as Streamlit widgets
# rendered directly below the banner.
# ---------------------------------------------------------------------------

def plan_banner(*, tone: Literal["amber", "green"], heading: str,
                subtext: str = "", chips: list[str] | None = None) -> str:
    """Colored home-screen banner card. tone 'amber' = plan in progress,
    'green' = confirmed plan. Render with st.html(); put the buttons below."""
    import html as _html
    bg, bd, fg, chip_bg, chip_bd, chip_fg = {
        "amber": (P["amber_50"], P["amber_300"], "#7a5300",
                  "#ffffff", P["amber_300"], "#7a5300"),
        "green": ("oklch(97% 0.03 150)", "oklch(87% 0.11 150)", P["green_900"],
                  "#ffffff", "oklch(87% 0.11 150)", P["green_900"]),
    }[tone]
    chip_html = ""
    if chips:
        pills = "".join(
            f'<span style="display:inline-block; font-size:12.5px; font-weight:600; '
            f'color:{chip_fg}; background:{chip_bg}; border:1px solid {chip_bd}; '
            f'border-radius:999px; padding:3px 11px; margin:0 6px 6px 0;">'
            f'{_html.escape(c)}</span>' for c in chips
        )
        chip_html = f'<div style="margin-top:10px; line-height:1.9;">{pills}</div>'
    sub = (f'<div style="font-size:13.5px; color:{fg}; opacity:0.85; '
           f'margin-top:2px;">{_html.escape(subtext)}</div>') if subtext else ""
    return (
        f'<div style="background:{bg}; border:1px solid {bd}; border-radius:14px; '
        f'padding:16px 18px; margin-bottom:6px;">'
        f'<div style="font-size:16px; font-weight:700; color:{fg};">'
        f'{_html.escape(heading)}</div>{sub}{chip_html}</div>'
    )


# ---------------------------------------------------------------------------
# Planner card — the slot / candidate / meal card anatomy from the hand-off:
# a compact RecipeArt (or photo) tile, a slot-label pill, the title with an
# optional favorite star, a meta line, and reason chips. The Preview / Replace
# (etc.) buttons stay as Streamlit widgets in an adjacent column.
# ---------------------------------------------------------------------------

def recipe_tile_html(recipe: dict, size: int = 54) -> str:
    """Raw HTML for a recipe's compact tile: the real photo if present, else
    the generated RecipeArt plate. For embedding inside a card HTML block
    (use render_thumb() when you want a standalone st.* element instead)."""
    box = (f'width:{size}px; height:{size}px; flex-shrink:0; border-radius:12px; '
           f'overflow:hidden; display:flex; align-items:center; justify-content:center;')
    if recipe and recipe.get("image_url"):
        import html as _html
        src = _html.escape(recipe["image_url"])
        return (f'<div style="{box}"><img src="{src}" alt="" '
                f'style="width:100%; height:100%; object-fit:cover;"></div>')
    cuisine = (recipe.get("cuisines") or [""])[0] if recipe else ""
    glyph = (recipe.get("glyph") or "🍽") if recipe else "🍽"
    return f'<div style="{box}">{recipe_art(glyph, cuisine, size=size)}</div>'


def planner_card(*, recipe: dict, label: str, title: str, meta: str = "",
                 chips_html: str = "", favorite: bool = False,
                 tile_size: int = 54) -> str:
    """Compose the planner card body (tile + pill + title + star + meta +
    chips) as one HTML block. Render with st.html() inside the card's main
    column; keep the action buttons in a separate Streamlit column."""
    import html as _html
    pill = (
        f'<span style="display:inline-block; font-size:11px; font-weight:700; '
        f'text-transform:uppercase; letter-spacing:0.05em; '
        f'color:{P["green_900"]}; background:oklch(97% 0.03 150); '
        f'border-radius:999px; padding:2px 9px; margin-bottom:5px;">'
        f'{_html.escape(label)}</span>'
    ) if label else ""
    star = (f'<span style="color:{P["amber_700"]}; margin-left:7px; '
            f'font-size:15px;">★</span>') if favorite else ""
    meta_html = (f'<div style="font-size:13px; color:{P["fg_muted"]}; '
                 f'margin-top:2px;">{_html.escape(meta)}</div>') if meta else ""
    return (
        f'<div style="display:flex; gap:14px; align-items:flex-start;">'
        f'{recipe_tile_html(recipe, tile_size)}'
        f'<div style="min-width:0; flex:1;">{pill}'
        f'<div style="font-size:18px; font-weight:700; letter-spacing:-0.01em; '
        f'color:{P["fg"]};">{_html.escape(title)}{star}</div>'
        f'{meta_html}{chips_html}</div></div>'
    )


# ---------------------------------------------------------------------------
# Stat card — home screen three-up. Each tile is a tone-coloured pastel
# block with a glyph chip in the corner.
# ---------------------------------------------------------------------------

Tone = Literal["green", "grape", "sky", "coral"]


def stat_card(*, tone: Tone, glyph: str, label: str, value: str | int, sub: str = "") -> str:
    """Inline-styled stat tile. Uses literal oklch() colors so it doesn't depend
    on :root CSS variables propagating through Streamlit's DOM (they don't, on
    some versions). Mirrors home.html mockup exactly."""
    bg, fg = {
        "green": ("oklch(93% 0.07 150)", "oklch(34% 0.10 148)"),
        "grape": ("oklch(94% 0.05 305)", "oklch(50% 0.16 305)"),
        "sky":   ("oklch(94% 0.05 220)", "oklch(54% 0.14 235)"),
        "coral": ("oklch(93% 0.07 25)",  "oklch(55% 0.18 25)"),
    }[tone]
    sub_html = (
        f'<div style="font-size:12px; color:{fg}; opacity:0.7; margin-top:4px;">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:{bg}; border-radius:12px; padding:16px 18px; '
        f'position:relative; overflow:hidden;">'
        f'<div style="position:absolute; top:12px; right:14px; width:28px; height:28px; '
        f'border-radius:999px; background:rgba(255,255,255,0.55); display:flex; '
        f'align-items:center; justify-content:center; font-size:14px;">{glyph}</div>'
        f'<div style="font-size:12px; font-weight:600; color:{fg}; opacity:0.75; '
        f'text-transform:uppercase; letter-spacing:0.4px;">{label}</div>'
        f'<div style="font-size:36px; font-weight:700; color:{fg}; line-height:1.05; '
        f'margin-top:6px;">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Savings card — sale-scan hero. Pass the alert dict from sale_scanner.
# The shape matches what scan_for_sale_alternatives() returns.
# ---------------------------------------------------------------------------

def savings_card(alert: dict) -> str:
    pref = alert["preferred_product"]
    sale = alert["sale_product"]
    item_name = alert["item_name"]
    sav_amt = float(alert["savings_amount"])
    sav_pct = float(alert["savings_pct"])

    pref_price = pref.get("price")
    sale_was = sale.get("price")
    sale_now = sale.get("promo_price") or sale.get("price")

    pref_img = pref.get("image_url") or _placeholder_box()
    sale_img = sale.get("image_url") or _placeholder_box()

    side_eyebrow_base = (
        f'font-size:11px; text-transform:uppercase; letter-spacing:0.4px; font-weight:600;'
    )

    return f'''
<div style="border:1px solid {P["border_soft"]}; border-radius:12px; overflow:hidden;
            background:{P["surface"]}; margin-bottom:16px;">
  <div style="background:{P["sale_bg"]}; padding:14px 20px; display:flex; align-items:center;
              justify-content:space-between; gap:18px; border-bottom:1px solid {P["amber_300"]};">
    <div>
      <div style="font-size:12px; font-weight:600; color:{P["sale_fg"]}; letter-spacing:0.4px;
                  text-transform:uppercase;">💰 Save on {item_name}</div>
      <div style="font-size:32px; font-weight:700; color:{P["sale_fg"]}; margin-top:2px;
                  line-height:1.05;">−${sav_amt:.2f}</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:12px; color:{P["sale_fg"]}; opacity:0.7;">that's</div>
      <div style="font-size:22px; font-weight:700; color:{P["sale_fg"]};">{sav_pct:.0f}% off</div>
    </div>
  </div>
  <div style="display:grid; grid-template-columns:1fr auto 1fr; align-items:stretch;">
    <div style="padding:16px 20px; display:flex; gap:12px; align-items:center;">
      {_img(pref_img)}
      <div>
        <div style="{side_eyebrow_base} color:{P["fg_muted"]};">Your usual</div>
        <div style="font-weight:600; font-size:14px; margin-top:2px; line-height:1.3;">{pref.get("brand", "")} {pref.get("product_name", "")}</div>
        <div style="color:{P["fg_muted"]}; font-size:13px; margin-top:2px;">{pref.get("size", "")} · <strong>${pref_price:.2f}</strong></div>
      </div>
    </div>
    <div style="display:flex; align-items:center; justify-content:center; color:{P["fg_subtle"]};
                font-weight:600; font-size:13px; padding:0 4px; font-family:ui-monospace,Menlo,monospace;">vs</div>
    <div style="padding:16px 20px; display:flex; gap:12px; align-items:center;
                background:{P["amber_50"]};">
      {_img(sale_img)}
      <div>
        <div style="{side_eyebrow_base} color:{P["sale_fg"]};">🏷 On sale</div>
        <div style="font-weight:600; font-size:14px; margin-top:2px; line-height:1.3;">{sale.get("brand", "")} {sale.get("product_name", "")}</div>
        <div style="color:{P["fg_muted"]}; font-size:13px; margin-top:2px;">{sale.get("size", "")} · <s style="color:{P["fg_subtle"]};">${sale_was:.2f}</s> <strong style="color:{P["sale_fg"]};">${sale_now:.2f}</strong></div>
      </div>
    </div>
  </div>
</div>
'''.strip()


# ---------------------------------------------------------------------------
# Product card — review queue primary product. Replaces the loose layout
# in main.py:_render_product_card with a styled card. Use BEFORE the
# Streamlit-native quantity/save-pref controls.
# ---------------------------------------------------------------------------

def product_card(product: dict) -> str:
    img = product.get("image_url") or _placeholder_box(140)
    brand = product.get("brand", "")
    name = product.get("product_name", "")
    size = product.get("size", "")
    price = product.get("price")
    promo = product.get("promo_price")
    on_sale = product.get("on_sale", False)
    oos = product.get("_oos_preferred") or product.get("in_stock") is False
    upc = product.get("upc")

    if on_sale and promo:
        price_html = (
            f'<s style="color:{P["fg_muted"]}; margin-right:8px; font-size:15px;">${price:.2f}</s>'
            f'<strong>${promo:.2f}</strong>'
            f'<span style="margin-left:8px; color:{P["amber_700"]}; font-size:12px;">🏷 On Sale</span>'
        )
    elif price:
        price_html = f'<strong>${price:.2f}</strong>'
    else:
        price_html = f'<span style="color:{P["fg_muted"]};">Price unavailable</span>'

    oos_html = (
        f'<div style="margin-top:6px; font-size:13px; color:{P["oos_fg"]};">🚫 <em>Out of Stock</em></div>'
        if oos else ""
    )
    upc_html = (
        f'<div style="font-family:ui-monospace,Menlo,monospace; font-size:11px; '
        f'color:{P["fg_subtle"]}; margin-top:8px; letter-spacing:0.4px;">UPC {upc}</div>'
        if upc else ""
    )

    # If img is an actual URL (not an HTML placeholder block), wrap with size + box styles.
    if isinstance(img, str) and img.startswith("http"):
        img_html = (
            f'<img src="{img}" alt="" style="width:140px; height:140px; object-fit:contain; '
            f'border-radius:8px; background:{P["bg_soft"]}; border:1px solid {P["border_soft"]}; '
            f'flex-shrink:0;">'
        )
    else:
        img_html = img

    return f'''
<div style="border:1px solid {P["border"]}; border-radius:8px; padding:18px; margin-bottom:8px;
            background:{P["surface"]}; box-shadow:0 1px 2px rgba(0,0,0,0.04); display:flex;
            gap:18px; align-items:flex-start;">
  {img_html}
  <div style="flex:1; min-width:0; padding-top:4px;">
    <div style="font-weight:600; font-size:18px; line-height:1.3;">{brand} {name}</div>
    <div style="color:{P["fg_muted"]}; font-size:14px; margin-top:4px;">{size}</div>
    {oos_html}
    <div style="margin-top:10px; font-size:18px;">{price_html}</div>
    {upc_html}
  </div>
</div>
'''.strip()


# ---------------------------------------------------------------------------
# Matching screen — row + progress header.
# Use during the spinner block in main.py's _run_matching_pipeline.
# ---------------------------------------------------------------------------

def matching_row(item_name: str, state: Literal["done", "in_flight", "queued"], brand: str = "") -> str:
    cls = {"done": "is-done", "in_flight": "is-flight", "queued": "is-queued"}[state]
    glyph = {"done": "✓", "in_flight": "···", "queued": "·"}[state]
    sub = brand if state == "done" else "searching Kroger…" if state == "in_flight" else "queued"
    return (
        f'<div class="sc-match-row {cls}">'
        f'<div class="sc-match-glyph">{glyph}</div>'
        f'<div style="min-width:0;">'
        f'<div class="sc-match-name">{item_name}</div>'
        f'<div class="sc-match-brand">{sub}</div>'
        f'</div></div>'
    )


def progress_section(*, matched: int, total: int, in_flight: int, queued: int) -> str:
    pct = round((matched / total) * 100) if total else 0
    return f'''
<div style="display:grid; grid-template-columns:1fr auto; gap:18px; align-items:flex-start;">
  <div>
    <h1 style="margin:0; font-size:32px; font-weight:700; letter-spacing:-0.01em;">🔎 Hunting these down…</h1>
    <div style="color:var(--sc-fg-muted); font-size:14px; margin-top:6px;">5 workers searching the Kroger catalog in parallel. Usually takes 10–15 seconds for a full list.</div>
  </div>
  <div style="text-align:right; background:var(--sc-green-100); padding:10px 16px; border-radius:12px; min-width:140px;">
    <div style="font-size:11px; font-weight:600; color:var(--sc-green-900); text-transform:uppercase; letter-spacing:0.4px; opacity:0.8;">matched</div>
    <div style="font-size:28px; font-weight:700; color:var(--sc-green-900); line-height:1.05; margin-top:2px;">
      {matched} <span style="color:var(--sc-green-700); opacity:0.6;">/ {total}</span>
    </div>
  </div>
</div>
<div style="margin-top:18px;">
  <div style="height:6px; background:var(--sc-border-soft); border-radius:999px; overflow:hidden;">
    <div style="height:100%; width:{pct}%; background:var(--sc-green-600); transition: width 240ms ease;"></div>
  </div>
  <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:13px; color:var(--sc-fg-muted);">
    <span><strong style="color:var(--sc-green-700);">{in_flight}</strong> in flight · <strong>{queued}</strong> queued</span>
    <span>🏷 Scanning for sale alternatives in parallel</span>
  </div>
</div>
'''.strip()


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _img(src_or_placeholder: str) -> str:
    """Either an <img src> or a literal placeholder block (📦 box)."""
    if src_or_placeholder.startswith("<"):
        return src_or_placeholder
    return f'<img src="{src_or_placeholder}" alt="">'


def _placeholder_box(size: int = 56) -> str:
    return (
        f'<div style="width:{size}px; height:{size}px; flex-shrink:0; '
        f'background:{P["bg_soft"]}; border-radius:6px; '
        f'border:1px solid {P["border_soft"]}; display:flex; '
        f'align-items:center; justify-content:center; font-size:{size//2}px;">📦</div>'
    )


# ---------------------------------------------------------------------------
# RecipeArt — deterministic generated graphic for recipes with no image_url.
# A cuisine-tinted tile with a white "plate" and the dish glyph; at larger
# sizes a plate ring, a faint steam mark, and hashed garnish dots. Same
# (glyph, cuisine) → identical art every time (handoff §"Recipe Art").
# ---------------------------------------------------------------------------

# Each palette = (tile bg, plate ring, accent) in the same oklch family as the
# design tokens. Distinct hues so a week of meals reads as varied at a glance.
_ART_PALETTES = {
    "coral": ("oklch(93% 0.07 25)",  "oklch(80% 0.12 25)",  "oklch(55% 0.18 25)"),
    "sky":   ("oklch(94% 0.05 220)", "oklch(80% 0.10 220)", "oklch(54% 0.14 235)"),
    "amber": ("oklch(95% 0.06 78)",  "oklch(82% 0.13 75)",  "oklch(58% 0.16 50)"),
    "grape": ("oklch(94% 0.05 305)", "oklch(82% 0.10 305)", "oklch(50% 0.16 305)"),
    "green": ("oklch(93% 0.07 150)", "oklch(82% 0.11 150)", "oklch(63% 0.18 148)"),
    "teal":  ("oklch(93% 0.06 190)", "oklch(80% 0.10 190)", "oklch(52% 0.13 195)"),
}

# Named cuisines → palette key. Anything else hashes onto one of the six below.
_CUISINE_PALETTE = {
    "chinese": "coral",
    "greek": "sky",
    "indian": "amber",
    "moroccan": "grape",
    "mexican": "green",
    "middle eastern": "teal",
}

_ART_ORDER = ("coral", "sky", "amber", "grape", "green", "teal")


def _art_hash(s: str) -> int:
    """Stable, process-independent hash (Python's hash() is salted per run)."""
    import hashlib
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)


def _art_palette_key(cuisine: str, glyph: str) -> str:
    key = (cuisine or "").strip().lower()
    if key in _CUISINE_PALETTE:
        return _CUISINE_PALETTE[key]
    # Unknown / multi cuisine → deterministic bucket from glyph+cuisine.
    return _ART_ORDER[_art_hash(f"{glyph}|{key}") % len(_ART_ORDER)]


def recipe_art(glyph: str = "🍽", cuisine: str = "", size: int = 54) -> str:
    """Return an inline SVG string: a cuisine-tinted plate placeholder for a
    recipe with no photo. Render with st.html(). Flat (no gradients), per the
    design system. Larger sizes add a plate ring, a faint steam mark, and
    hashed garnish dots so the header art feels intentional."""
    glyph = glyph or "🍽"
    bg, ring, accent = _ART_PALETTES[_art_palette_key(cuisine, glyph)]
    s = size
    cx = cy = s / 2
    plate_r = s * 0.36
    radius = s * 0.18
    big = s >= 72

    parts = [
        f'<svg width="{s}" height="{s}" viewBox="0 0 {s} {s}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'style="display:block; border-radius:{radius:.1f}px;">',
        f'<rect x="0" y="0" width="{s}" height="{s}" rx="{radius:.1f}" fill="{bg}"/>',
    ]

    if big:
        # Steam mark — a faint ♨-style double wisp above the plate.
        sw = max(1.0, s * 0.018)
        y0 = cy - plate_r - s * 0.04
        for dx in (-s * 0.07, s * 0.07):
            x = cx + dx
            parts.append(
                f'<path d="M {x:.1f} {y0:.1f} q {s*0.05:.1f} {-s*0.05:.1f} 0 {-s*0.10:.1f} '
                f'q {-s*0.05:.1f} {-s*0.05:.1f} 0 {-s*0.10:.1f}" '
                f'fill="none" stroke="{accent}" stroke-width="{sw:.1f}" '
                f'stroke-linecap="round" opacity="0.28"/>'
            )

    # White plate (with ring at larger sizes).
    ring_w = max(1.0, s * 0.03)
    parts.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{plate_r:.1f}" fill="#ffffff" '
        + (f'stroke="{ring}" stroke-width="{ring_w:.1f}"/>' if big else '/>')
    )
    if big:
        # Inset plate ring.
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{plate_r*0.72:.1f}" '
            f'fill="none" stroke="{ring}" stroke-width="{max(1.0, s*0.012):.1f}" '
            f'opacity="0.5"/>'
        )
        # 4–6 garnish dots placed by a hash of glyph+cuisine.
        h = _art_hash(f"{cuisine}|{glyph}|garnish")
        n_dots = 4 + (h % 3)  # 4..6
        import math
        for i in range(n_dots):
            a = (h >> (i * 3)) % 360
            rad = plate_r * (0.78 + ((h >> (i * 2)) % 10) / 50.0)  # 0.78..0.96
            dx = math.cos(math.radians(a)) * rad
            dy = math.sin(math.radians(a)) * rad
            dot_r = s * (0.018 + ((h >> i) % 3) * 0.006)
            parts.append(
                f'<circle cx="{cx+dx:.1f}" cy="{cy+dy:.1f}" r="{dot_r:.1f}" '
                f'fill="{accent}" opacity="0.55"/>'
            )

    # Dish glyph, centered on the plate.
    parts.append(
        f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
        f'dominant-baseline="central" font-size="{s*0.42:.1f}">{glyph}</text>'
    )
    parts.append('</svg>')
    return "".join(parts)
