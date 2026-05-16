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

### Rotating credentials
- **Anthropic key**: console.anthropic.com → keys → revoke + replace → update `.env` + Streamlit secrets
- **Kroger client secret**: developer.kroger.com → app → regenerate → same two places
- **Supabase service key**: dashboard → Settings → API → roll → same two places
- **APP_PASSWORD**: just edit both places

## Enhancement backlog
Rough priority order. Pick from the top.

### Out-of-the-gate polish
1. **Product images on review cards** — Kroger API returns `image_url`; product cards in `main.py:_render_product_card` ignore it. Adding `st.image(product['image_url'], width=120)` would change the entire feel.
2. **Mobile layout** — Streamlit's default columns crush awkwardly on a phone. The review queue (one card at a time) is fine; the home screen's metric cards and the Sale Scan two-column compare break. Need `st.container` + conditional column ratios or wider single-column on narrow viewports.
3. **Match badge restyle** — current colored boxes look like 2018 Bootstrap. Use Streamlit's native `st.badge` (added ~1.40) or replace the CSS with a flatter, larger pill style.
4. **Tighter review-queue vertical rhythm** — too much whitespace between alt cards. Drop padding in `.product-card` CSS.
5. **Empty/loading states** — most screens show "" or a spinner. Add skeletons for the parse → match transition so the user sees what's about to happen.

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
- **Kroger API rate limits aren't published precisely.** 5 parallel workers is conservative. If you see 429s, drop `MATCH_WORKERS` and `SCAN_WORKERS`.

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
*Last full audit: migration from local-only → Streamlit Cloud + Supabase. Single-user household tool. Not for distribution.*
