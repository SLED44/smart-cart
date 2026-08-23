# SmartCart — Claude Onboarding

> Loaded automatically by Claude Code in this directory. Quick context for future sessions.

## What this is
Single-user Streamlit app that turns a freeform grocery list into a Kroger (City Market) cart. Paste list → Claude Haiku parses + categorizes → Kroger Products API matched against household preferences → user reviews each item → posts to Kroger cart → user does checkout in the City Market app. Lives at https://sled44-smartcart.streamlit.app/ — owner is the only user.

## Stack
| Layer | Tech | Notes |
|---|---|---|
| UI | Streamlit | 1500-line `main.py` is all screens + router |
| AI | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | List parsing + best-match selection |
| Grocery | Kroger Public API | OAuth 2.0 + PKCE for cart writes; client_credentials for location search |
| Persistence | Supabase Postgres, single `kv (key text pk, value jsonb)` table | Project ref `odwkznptayhobwjgegin` in SLED44 org, us-west-1, free tier |
| Hosting | Streamlit Community Cloud (free) | Public visibility; `APP_PASSWORD` gates the app itself |
| Repo | https://github.com/SLED44/smart-cart (private) | `main` branch auto-deploys |

## Module map
| File | Responsibility |
|---|---|
| `main.py` | Streamlit screens, navigation, OAuth callback handler, `st.secrets → os.environ` bridge |
| `supabase_kv.py` | `kv_get` / `kv_put` / `kv_delete` via PostgREST. All persistence flows through here. |
| `preference_store.py` | Domain interface over KV: preferences, staples, session log, export/import |
| `kroger_auth.py` | OAuth (hosted flow + local CLI fallback). Tokens persist in Supabase. `NeedsAuthorization` exception. |
| `list_parser.py` | Claude call to normalize raw text → structured items |
| `product_matcher.py` | Per-item Kroger search + Claude best-match. Parallel: `ThreadPoolExecutor(5)`. |
| `sale_scanner.py` | Pre-review pass for on-sale alternatives to preferred items. Parallel: 5 workers. |
| `cart_manager.py` | Posts confirmed items to Kroger cart. |
| `sc_design.py` | Design-system helpers — return HTML strings for stat tiles, savings hero, product cards, badges. Render with `st.html()`, NOT `st.markdown()`. |
| `style.css` | Full design-system CSS. Loaded once at startup. See Design-system notes below. |
| `.streamlit/config.toml` | Streamlit native-widget theme (primary green for buttons, sliders). |

## How to update
1. Edit files locally.
2. (Optional) Local smoke test: `streamlit run main.py` after `source .venv/bin/activate`.
3. Commit + push:
   ```bash
   git add -A && git commit -m "what changed" && git push
   ```
4. Streamlit Cloud auto-deploys within ~30s. Logs: https://share.streamlit.io/ → app → Manage app.

### Secrets (two places)
- **Local dev**: `.env` (gitignored, has real values)
- **Streamlit Cloud**: app settings → Secrets (TOML format, see `.streamlit/secrets.toml.example`)

`main.py` bridges `st.secrets → os.environ` so backend modules can stay env-var based. `load_dotenv(override=True)` because the host shell may have stale empty values for `ANTHROPIC_API_KEY`.

### Touching the database
Schema lives in the Supabase dashboard. Single `kv` table is all we need; if you add another table, do it through a SQL migration in the dashboard and document it here.

### Design system
The visual layer was built from a Claude Design hand-off (May 2026). Architecture:

- **`style.css`** holds the full token system: `:root` palette (oklch), type scale, spacing tokens, and all `.sc-*` class definitions (`.sc-stat-card`, `.sc-savings-card`, `.sc-match-row`, etc.).
- **`sc_design.py`** contains Python functions that return HTML strings for each visual primitive. Call from any screen with `st.html(stat_card(...))`.
- **`.streamlit/config.toml`** sets Streamlit's native widget theme so buttons/sliders/checkboxes match the design-system green.

**Critical rendering rules** (learned the hard way):
1. **Use `st.html()`, not `st.markdown()`** for any HTML primitive. `st.markdown` runs the content through a markdown parser that mangles `*` characters in CSS comments and inserts unexpected tags.
2. **Never put a raw `<` in `style.css` — not even inside a comment.** `st.html` sanitizes with DOMPurify, whose `SAFE_FOR_XML` rule (on by default) force-removes any element whose text contains `<` followed by `/`, `!`, or a word character. One literal `<style>` in a comment silently deletes the **entire** stylesheet — no error, no partial styling. `main.py`'s `_css_safe()` defensively rewrites every `<` as the CSS escape `\3c `, which the CSS parser treats as identical.
3. **`:root` CSS variables, `oklch()`, and `.sc-*` class rules all work fine.** Once the stylesheet actually lands, vars resolve normally throughout Streamlit's DOM.

**History (corrected 2026-07-23).** From the May 2026 refresh until 2026-07-23, `style.css` never loaded at all: its banner comment contained a literal `<style>`, so DOMPurify dropped all 10KB. The UI only looked right because `sc_design.py` writes literal inline styles on every element. Two notes in this file blamed `:root` inheritance for that; both were wrong and have been rewritten. Bisected on Streamlit 1.54 with a scratch probe app: `< b` and `<--` survive; `<style>`, `<!--`, and `<3` do not. Size, `@media`, and `oklch()` were all ruled out — an 11KB filler stylesheet loads fine.

To add a new visual primitive you can now use either approach: a `.sc-*` class in `style.css`, or inline `PALETTE` colors in an `sc_design.py` helper. Existing helpers are all inline-styled; see the DRY-the-colors backlog item.

### Rotating credentials
- **Anthropic key**: console.anthropic.com → keys → revoke + replace → update `.env` + Streamlit secrets
- **Kroger client secret**: developer.kroger.com → app → regenerate → same two places
- **Supabase service key**: dashboard → Settings → API → roll → same two places
- **APP_PASSWORD**: just edit both places

## Enhancement backlog
Rough priority order. Pick from the top.

### Shipped 2026-05-16 (efficiency + bug pass)
- ✅ Pack-size quantity bug — "4 eggs" no longer becomes 4 cartons. `_adjust_quantity_for_pack_size` now does `ceil(N / pack_count)` so 4 eggs → 1 carton, 24 → 2.
- ✅ Skip-LLM shortcuts in `product_matcher._try_shortcut_match`: single Kroger result → use it; top result unambiguously matches item name (all words present, no other top-5 result also matches) → use it. Both skip the Claude call entirely.
- ✅ Trim Claude's candidate list from 10 → 5 products (search is already relevance-sorted; positions 6-10 effectively never get picked).
- ✅ Compact matcher system prompt from ~1,000 chars → ~460.
- Combined effect: ~60% fewer LLM tokens per session on typical lists. Matching also faster (fewer round-trips).

### Shipped in May 2026 design refresh
- ✅ Product images on review cards (`sc_design.product_card`, 140px)
- ✅ Match badge restyle (pastel pills, palette in `sc_design.py`)
- ✅ Voice copy across home / item filter / matching spinner / sale scan / summary
- ✅ Pastel stat tiles on home screen (replaces `st.metric`)
- ✅ Sale-scan savings hero card
- ✅ Streamlit native widget theme (`.streamlit/config.toml`)

### Carried over from Claude Design hand-off (NOT shipped)
1. **Matching screen progress UI** — currently a basic spinner. Mocks in the design hand-off show a step-by-step grid of `matching_row` rendering as items resolve. Requires threading callback in `product_matcher.match_items` so the UI can render mid-flight. Helpers `matching_row` and `progress_section` are already in `sc_design.py` waiting to be wired.
2. **Preferences screen redesign** — Claude Design has richer mocks: per-row product cards, drag handles, qty steppers, search bar with inline edit. Current screen is functional but plain.
3. **Staples screen redesign** — same situation, mocks exist for category grouping + drag-to-reorder.
4. **Tablet/mobile pass** — no responsive rules exist yet. (This item used to claim `style.css` had a `@media (max-width: 760px)` block that Streamlit's chrome overlaid; there is no such block — corrected 2026-07-23.) Needs an actual `@media` block in `style.css`, plus `st.set_page_config(layout="centered")` and tighter padding overrides.
5. **Step pills in header bar** — the mockup shows a 6-step progress indicator (Paste → Trim → Match → Deals → Review → Done) across the top of every screen. Not implemented.

### Out-of-the-gate polish (still relevant)
1. **Empty/loading states** — most screens show "" or a spinner. Add skeletons for the parse → match transition so the user sees what's about to happen.
2. **Tighter review-queue vertical rhythm** — too much whitespace between primary card and alt cards. Drop padding in product card / inline section margins.

### Design-system cleanup (DRY the colors)
**Status**: unblocked 2026-07-23 — not yet done. Every `sc_design.py` helper repeats oklch literals inline. The old premise for that ("Streamlit's DOM doesn't inherit `:root` CSS vars") was wrong; the real problem was that `style.css` never loaded. It loads now, and `var(--sc-*)` off `:root` resolves correctly — verified in the running app. So the fix is just: replace the literal colors in `sc_design.py` with `var(--sc-*)` references, keeping `PALETTE` as the fallback map. No `.stApp` scoping needed. Worth doing on a branch and eyeballing each screen, since ~40 rules went live at once when the stylesheet was fixed.

### Quick wins (≤30 min each)
6. **Auto-detect expired Kroger refresh token on home screen** and surface the Connect Kroger banner proactively rather than mid-match.
7. **Edit-a-preference UI** — `preference_store.update_preference_upc()` exists but no screen uses it. Add an "Edit" button next to Delete on the Preferences screen.
8. **Repeat last order** — one-click button on home: pulls last `session_log` entry's items and pre-populates the textarea or jumps straight to review.
9. **Staple drag-to-reorder** — `reorder_staples()` exists; UI doesn't.
10. **Cache Kroger search results in-session** — same list twice in a row currently re-queries everything. Use `@st.cache_data(ttl=300)` on `_search_kroger_products`.

### Medium
11. **Pantry inventory** — third KV bucket. Item filter screen prefilters items already on hand.
12. **Recipe → list** — paste a recipe URL or text, Claude extracts ingredient list. New screen + one more `claude.messages.create` call.
13. **Price history per UPC** — write every observed price to a `price_history` table (would need a new Supabase table, not just KV). Surface a sparkline on the review card.
14. **Backup automation** — nightly export to email/Dropbox so the user never has to remember the Backup & Restore button.
15. **Brute-force protection on login** — current password check has no rate limit. 3 failed attempts → cooldown.

### Architecture cleanup (only worth doing if the app keeps growing)
16. **Split `main.py`** into a `screens/` package — one file per screen, router stays in `main.py`. Currently 1500 lines.
17. **Split `product_matcher.py`** (850 LOC) into `kroger_api.py` (HTTP layer), `claude_select.py` (LLM prompt + parsing), `matching.py` (orchestration).
18. **Replace `print()` with `logging`** — would surface nicely in Streamlit Cloud's structured log view.
19. **Tests** — there's only one self-test in `preference_store.py --test`. Worth pytest skeletons for `list_parser`, `product_matcher` (with mocked Kroger), `cart_manager` (with mocked Kroger).
20. **Type hints + ruff** — partial coverage; not a blocker but would catch a class of bugs (the `add_staples` NameError caught during the migration was a textbook case).

### Speculative / nice-to-haves
21. Multi-store price comparison (Kroger + Safeway + Target).
22. Voice input → text via Whisper.
23. Browser extension that scrapes recipe sites' "Add to grocery list" buttons.
24. Per-item household member tagging ("Lily's snacks").

## Known quirks future-you will hit
- **Kroger OAuth redirect URI must match exactly** in two places: `KROGER_REDIRECT_URI` (Streamlit secrets) and the Kroger developer portal app config. Trailing slash matters. If you ever change the Streamlit URL, update both.
- **OAuth state is stored in Supabase** (`oauth_pending:<state>` keys), not Streamlit `session_state` — the latter doesn't survive the external redirect. Don't refactor this without understanding why.
- **Streamlit free tier kills containers on inactivity** — first request after a few hours takes ~10s cold start. Acceptable for weekly shopping.
- **Service-role key bypasses RLS**. RLS is enabled as a safety net but no policies exist. If you add other clients (mobile app, browser extension), introduce anon-key + RLS policies before exposing it.
- **`load_dotenv(override=True)`** is intentional — the user's shell exports `ANTHROPIC_API_KEY=` (empty) from Claude Desktop, which would otherwise silently shadow the `.env` value.
- **Quantity from notes only applies to items the list didn't count.**
  `product_matcher.suggested_quantity()` reads an item's notes as the *total*
  amount wanted ("2 lbs" ÷ a 12 oz pack → 3), which is only true for the shape
  the parser gives butcher/deli items (quantity 1, unit "", weight in notes).
  Meal-plan lines like `2 can cannellini beans (15 oz)` parse to quantity 2
  with "15 oz" in notes — there the size is ONE can, and dividing collapsed
  2 cans to `ceil(15 / 15.5)` = 1. Worse, items with a saved preference are
  auto-confirmed (`screens/_shared.split_auto_confirmed`), so it happened with
  no review card to catch it. Every call site now goes through
  `product_matcher.coverage_quantity(item, product)`, which returns None for
  anything the parser counted (quantity > 1). Fixed 2026-08-23; regression
  tests in `python3 product_matcher.py --test`.
- **Kroger API rate limits aren't published precisely.** 5 parallel workers is conservative. If you see 429s, drop `MATCH_WORKERS` and `SCAN_WORKERS`.
- **Supabase free-tier pausing can't be reliably prevented — so the keepalive self-heals instead.** Learned July 2026 in two stages: (1) read-only GET pings don't count as "sufficient activity"; (2) even a daily *verified write* didn't stop the 2026-07-08 pause — Supabase paused the project one minute after a successful write, because once their scanner flags a project the pause proceeds anyway. `.github/workflows/keepalive.yml` (v3) runs daily (09:23 UTC), upserts kv key `keepalive:last_ping` and verifies it via a per-run nonce; if the write fails because the project is paused, it **auto-restores** via the Supabase Management API, waits for `ACTIVE_HEALTHY`, and retries. It also re-enables itself via the GitHub API to reset GitHub's 60-day scheduled-workflow disable timer. GitHub only emails a failure when auto-restore itself fails — that's when a human needs https://supabase.com/dashboard/project/odwkznptayhobwjgegin. Unpausing is **free** within 90 days of a pause (never a paid unlock); past 90 days the project is unrecoverable (data export only). Required repo secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ACCESS_TOKEN` (personal access token for the restore path).
- **Streamlit CSS gotchas** to remember — all of them are the sanitizer, and all of them fail *silently*:
  - **A raw `<` anywhere in a CSS payload kills the whole `<style>` element.** DOMPurify's `SAFE_FOR_XML` removes any element whose text has `<` followed by `/`, `!`, or a word char. A `<style>` inside a `/* comment */` is enough. This made `style.css` completely inert from May 2026 to 2026-07-23. `main.py`'s `_css_safe()` rewrites `<` → `\3c ` to immunize it. Size, `@media`, and `oklch()` are *not* problems — an 11KB filler sheet loads fine.
  - `st.markdown` mangles CSS (interprets `*` as emphasis). Use `st.html` for any raw HTML/CSS injection. `st.markdown` also truncates at the first literal `</style>`, which `st.html` does not.
  - `:root` CSS variables **do** propagate fine, as do `oklch()` colors and `.sc-*` class rules. Earlier notes here claimed otherwise; that was a misdiagnosis of the `<`-in-comment bug.
  - `st.html`'s sanitizer silently strips inline `<svg>` — SVG renders as blank space, no error. Build art from plain styled `<div>`s instead (see `sc_design.recipe_tile_html`). Inline SVG *does* work inside `st.components.v1.html` iframes (not sanitized) — that's where `sc_design.recipe_art` is still used (cook-pane).
  - Debugging tip: `st.html` sends style-only payloads to Streamlit's *event* container, not the main tree, so don't panic when the `<style>` isn't where you expect. To check whether a sheet actually landed, look for one of its selectors in `document.styleSheets` rather than for the tag.

## Useful one-liners

```bash
# Smoke test everything
python3 supabase_kv.py && python3 preference_store.py --test

# Pull a fresh local backup from Supabase
python3 -c "import json, preference_store; print(json.dumps(preference_store.export_data(), indent=2))" > backup_$(date +%Y%m%d).json

# Re-auth Kroger locally (rarely needed — hosted flow self-heals)
python3 kroger_auth.py --reauth

# Tail Streamlit Cloud logs
# (no CLI — use https://share.streamlit.io/ → app → Manage app → Logs panel)
```

---
*Last update: 2026-07-23 — fixed `style.css` never loading (DOMPurify dropped it over a literal `<style>` in a comment); corrected the Design-system notes that had blamed `:root` inheritance. Previous: 2026-05-16 — Claude Design system refresh (pastel stat tiles, savings hero, refreshed product cards, voice copy pass). Previous: 2026-05-15 migration from local-only → Streamlit Cloud + Supabase. Single-user household tool. Not for distribution.*
