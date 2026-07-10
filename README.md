# SmartCart
**Meal Planner + AI-Powered Grocery → Kroger Cart Automation**

SmartCart plans the week's dinners and gets the groceries into your Kroger cart. The **Meal Planner** — the post-login landing page — proposes N recipes from a self-hosted recipe library while respecting your household rules (protein limits, cuisine variety, favorites cadence, exclusions). You keep or swap each suggestion, confirm the lineup, and the planner aggregates the recipes' ingredients into a grocery list. That list flows into the **SmartCart grocery pipeline** — the checkout step: each item is matched to real Kroger products using your saved household preferences, you review and confirm item-by-item, and SmartCart posts everything to your City Market cart. You complete pickup time and payment in the City Market app.

The grocery pipeline also works standalone: paste any freeform list under the Grocery tab and it goes through the same match → review → cart flow.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Deploy to Streamlit Cloud (recommended)](#2-deploy-to-streamlit-cloud-recommended)
3. [Local development](#3-local-development)
4. [Using the app](#4-using-the-app)
5. [File reference](#5-file-reference)
6. [Troubleshooting](#6-troubleshooting)
7. [Security reminders](#7-security-reminders)
8. [Cost reference](#8-cost-reference)

---

## 1. Architecture

| Layer | Tech |
|---|---|
| UI | Streamlit (Python) — router in `main.py`, one module per screen under `screens/` |
| Meal planning | Pure deterministic Python (`mealplan/` package) — **no LLM calls** |
| Recipe sourcing | Spoonacular API (bootstrap + discovery), cached into Supabase; external recipes pasted in as normalized JSON |
| AI | Claude Haiku 4.5 via Anthropic SDK — grocery list parsing + product match selection only |
| Grocery data | Kroger Public API (OAuth 2.0 + PKCE) |
| Persistence | Supabase Postgres (single `kv` table — recipes, plans, rules, preferences, staples, session log, tokens, location) |
| Hosting | Streamlit Community Cloud (free) |
| CI | GitHub Actions — meal-planner test suite on every push/PR, plus a daily Supabase keep-alive |

All persistent state lives in Supabase so the app survives container restarts on hosts with ephemeral disk.

The two features share one design decision worth knowing: the meal-plan grocery output is already structured (categorized, with quantities), so the hand-off to the grocery pipeline is in-process state injection — it skips the Claude parse step entirely. Unit rounding is shared between the recipe display and the grocery aggregator via `recipe_units.py`, so the shopping list and the cook screen always agree.

---

## 2. Deploy to Streamlit Cloud (recommended)

### Step 1 — Get your secrets ready

You need values for all of these:

| Variable | Where to get it |
|---|---|
| `KROGER_CLIENT_ID` | https://developer.kroger.com → your app |
| `KROGER_CLIENT_SECRET` | same |
| `KROGER_REDIRECT_URI` | will be `https://<your-app>.streamlit.app/` — see Step 3 |
| `KROGER_LOCATION_ID` | leave blank; pick a store in-app after first login |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → Settings → API Keys |
| `SPOONACULAR_API_KEY` | https://spoonacular.com/food-api (free tier; recipe library bootstrap + discovery) |
| `APP_PASSWORD` | a password you choose for the login screen |
| `SUPABASE_URL` | Supabase dashboard → Project Settings → API |
| `SUPABASE_SERVICE_KEY` | same page, the `service_role` secret (NOT the anon key) |

### Step 2 — Push the repo to GitHub

```bash
cd ~/Documents/Claude\ Projects/smart\ cart
git init
git add .
git commit -m "Initial commit"
gh repo create smart-cart --private --source=. --push   # or use the GitHub web UI
```

The included `.gitignore` keeps `.env`, `.streamlit/secrets.toml`, and local working data out of the repo.

### Step 3 — Deploy on Streamlit Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **New app**, point it at your repo, branch `main`, file `main.py`.
3. Click **Deploy**. The first deploy fails on missing secrets — that's expected.
4. Note the URL Streamlit assigned, e.g. `https://your-app-name.streamlit.app/`.
5. Click **⋮ → Settings → Secrets** and paste the template from [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) with real values filled in. Set `KROGER_REDIRECT_URI` to your full Streamlit Cloud URL **including the trailing slash**.
6. Save. The app restarts automatically.

### Step 4 — Update Kroger redirect URI

In the [Kroger Developer Portal](https://developer.kroger.com), edit your app and set the **Redirect URI** to your Streamlit Cloud URL — must match `KROGER_REDIRECT_URI` exactly (including trailing slash).

### Step 5 — First-time setup

1. Open the app URL, log in with `APP_PASSWORD`. You land on the Meal Planner home.
2. Bootstrap the recipe library (Meal Planner → Library → bootstrap pull from Spoonacular; the free tier's daily quota covers it).
3. Review the planner rules (Meal Planner → Rules) — household size, protein limits, cuisine variety, exclusions.
4. Switch to the Grocery tab: **Connect Kroger** → authorize on Kroger → you land back in the app.
5. Click **Find My Store**, enter your zip, pick your City Market. Selection persists in Supabase.
6. Go to **Staples** and add your 10-20 weekly recurring items.
7. Plan a small test week (or run a 3-item pasted list) to verify the full flow end to end.

---

## 3. Local development

```bash
cd ~/Documents/Claude\ Projects/smart\ cart
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in real values
streamlit run main.py
```

Run the meal-planner test suite (network-free — no Supabase, no LLM):
```bash
python3 -m mealplan._tests
```

For local Kroger OAuth:
- Set `KROGER_REDIRECT_URI=http://localhost:8501/` in `.env`
- Register that exact URI in the Kroger developer portal (you can register multiple)
- In the app, click **Connect Kroger** — it works the same way as on Streamlit Cloud

To re-authorize from the CLI (useful when debugging):
```bash
python3 kroger_auth.py --reauth
```

---

## 4. Using the app

### Weekly meal plan → groceries (the main flow)

1. Open the app URL, log in. You land on **Meal Planner home**.
2. Click **Plan meals** and choose how many dinners you want (default 5).
3. The planner proposes a lineup of recipe cards — title, cuisine, protein, prep+cook time, last-made date. An optional **"Why these picks?"** expander shows how the rules shaped the lineup.
4. For each card, **Keep** it or **Replace** it. Replacing surfaces alternative candidates (filtered by cuisine/protein, or unfiltered). You can also regenerate the whole lineup.
5. Confirm the plan. The planner aggregates all ingredients into a categorized grocery list — quantities summed across recipes and rounded to shopper-friendly amounts (whole cans, quarter-cups, half-pounds).
6. The list lands in the SmartCart grocery pipeline (the checkout step): review the items, optionally add staples, then **Find Products**.
7. If the Sale Scan screen appears, review on-sale alternatives and decide whether to switch.
8. Work through the review queue one item at a time — swap alternatives, save preferred choices, adjust quantities, **Add to Cart**.
9. After the last item, SmartCart posts everything to your Kroger cart.
10. Click **Open City Market Cart** → pick pickup time → confirm payment → place order.

### During the week: cooking mode

Meal Planner → your active plan → open a recipe to cook from it (scaled quantities, step-by-step). Afterwards record the outcome — **made it / made changes / never again** — which feeds back into future plans (favorites cadence, retirement).

### Growing the library

- **Spoonacular discovery** — search and pull new recipes in-app; results are cached into Supabase.
- **Paste a recipe** — recipes from anywhere (NYT Cooking, family recipes) normalized to the app's JSON schema via a Claude.ai chat externally, then pasted into Meal Planner → Paste recipe. A validator gates what gets saved.

### Standalone grocery run (no meal plan)

Grocery tab → paste your list → **Parse List** (Claude structures it) → same match → sale scan → review → cart flow as above.

### Match badges

| Badge | Meaning |
|-------|---------|
| Preferred Match | Your saved preference was found in stock |
| Preferred OOS | Your preferred product is out of stock — substitute shown |
| Best Match | No preference saved — Claude picked the best result |
| Needs Your Pick | Low confidence match — review carefully |
| Not Found | No Kroger product found — skip or add manually in City Market |
| On Sale Alt | You switched to a sale alternative on the Sale Scan screen |

### Backup & restore

Preferences page → **Backup & Restore** expander. Download a JSON snapshot any time; upload it to restore.

---

## 5. File reference

| File | Purpose |
|------|---------|
| `main.py` | Streamlit router — session init, OAuth callback handler, screen dispatch |
| `screens/` | One module per screen: meal-plan home/propose/swap/active/rules/library/cook, grocery preview/review/summary, preferences, staples, login, … |
| `mealplan/` | Meal-planner engine: `planner.py` (lineup generation), `rules.py` (ruleset + evaluation), `library.py` (recipe storage), `grocery.py` (ingredient aggregation), `swap.py`, `spoonacular.py`, `bootstrap.py`, `event_log.py`, `_tests.py` |
| `recipe_units.py` | Shared unit-rounding/discrete-unit logic used by both the recipe display and the grocery aggregator |
| `list_parser.py` | Parses raw grocery list text via Claude API (pasted lists only — meal-plan hand-off skips this) |
| `product_matcher.py` | Matches items to Kroger products (parallel; 5 workers) |
| `sale_scanner.py` | Scans for on-sale alternatives (parallel; 5 workers) |
| `cart_manager.py` | Posts confirmed items to Kroger cart |
| `preference_store.py` | Single source of truth for persistent data — Supabase-backed |
| `kroger_auth.py` | Kroger OAuth 2.0 + PKCE (hosted-friendly) |
| `supabase_kv.py` | Tiny key-value layer over the Supabase `kv` table |
| `sc_design.py` + `style.css` | Design system — HTML/CSS helpers rendered with `st.html()` |
| `applog.py` | Logging setup (shows up in Streamlit Cloud's log view) |
| `scripts/archive/` | One-shot migration scripts kept for reference (see its README) |
| `.github/workflows/` | CI: `tests.yml` (meal-planner suite on push/PR), `keepalive.yml` (daily Supabase keep-alive + auto-restore) |
| `requirements.txt` | Python dependencies |
| `.env.example` | Local config template |
| `.streamlit/secrets.toml.example` | Streamlit Cloud secrets template |

---

## 6. Troubleshooting

### "Connect Kroger" loops back without authorizing
- The redirect URI registered in the Kroger developer portal doesn't exactly match `KROGER_REDIRECT_URI`. Both must include the trailing slash and use the same scheme (`https://` on the cloud, `http://` locally).

### Recipes/preferences/staples disappeared
- Almost certainly a Supabase config issue — `SUPABASE_URL` or `SUPABASE_SERVICE_KEY` is wrong or pointing at a different project. If Supabase paused the free-tier project, the keep-alive workflow auto-restores it (see `.github/workflows/keepalive.yml`); you only need the Supabase dashboard if that workflow emails a failure. Preferences can be restored from a backup snapshot via Preferences → Backup & Restore.

### Streamlit Cloud "Secrets not found" or import errors
- Open the Streamlit Cloud dashboard → your app → **Manage app → Logs**. Most failures show up there.

### Kroger token expired and won't refresh
- From the home screen, click **Connect Kroger** to re-authorize. Refresh tokens live for ~6 months; re-auth is fast.

### No locations found when searching for store
- Kroger's locations API can be finicky with city names. Try a zip code instead.

### Items posting to cart but not appearing in City Market
- Kroger cart sync can take 30-60 seconds. Refresh the City Market cart page. If items still don't appear, use **Retry Failed Items** on the session summary screen.

### Spoonacular quota exhausted
- Free tier is 150 points/day; the library bootstrap uses most of a day's quota. Discovery pulls resume the next day.

---

## 7. Security reminders

- The Supabase **service_role** key bypasses RLS. Treat it like a database password — paste it only into Streamlit Cloud's secrets manager or your local `.env`. Never commit it.
- The household `APP_PASSWORD` is the only thing protecting the public Streamlit URL from anyone on the internet. Pick a strong one.
- Kroger OAuth scope is `product.compact cart.basic:write` — no access to payment info or order history.
- Set an Anthropic spending cap of $10/month at console.anthropic.com.

---

## 8. Cost reference

| Item | Monthly cost | Notes |
|---|---|---|
| Streamlit Community Cloud | Free | Public app URL, ephemeral container |
| Supabase free tier | Free | 500 MB Postgres — we use < 1 MB; daily keep-alive prevents/repairs pausing |
| Kroger Public API | Free | Builder Tier, 500k credits/month, SmartCart uses < 1k |
| Spoonacular | Free | 150 points/day tier — bootstrap + occasional discovery |
| Claude Haiku 4.5 | ~$1-3 | Grocery parsing + matching only; the meal planner makes zero LLM calls |
| **Total** | **~$1-3/month** | Set Anthropic spending cap to $10 as a ceiling |

---

*SmartCart v3 — Meal Planner + grocery hand-off, Streamlit Cloud + Supabase. For household use. Not for distribution.*
