# SmartCart
**AI-Powered Grocery List → Kroger Cart Automation**

SmartCart takes a grocery list, matches each item to real Kroger products using your saved household preferences, lets you review and confirm each item, then populates your City Market cart automatically. You complete checkout — pickup time and payment — directly in the City Market app.

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
| UI | Streamlit (Python) |
| AI | Claude Haiku 4.5 via Anthropic SDK |
| Grocery data | Kroger Public API (OAuth 2.0 + PKCE) |
| Persistence | Supabase Postgres (single `kv` table — preferences, staples, session log, tokens, location) |
| Hosting | Streamlit Community Cloud (free) |

All persistent state lives in Supabase so the app survives container restarts on hosts with ephemeral disk.

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

The included `.gitignore` keeps `.env`, `.streamlit/secrets.toml`, and `data/` out of the repo.

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

1. Open the app URL, log in with `APP_PASSWORD`.
2. The home screen will prompt you to **Connect Kroger** → click the button → authorize on Kroger → you land back on the home screen.
3. Click **Find My Store**, enter your zip, pick your City Market. Selection persists in Supabase.
4. Go to **Staples** and add your 10-20 weekly recurring items.
5. Run a 3-item test list to verify the full flow before your first real shopping run.

---

## 3. Local development

```bash
cd ~/Documents/Claude\ Projects/smart\ cart
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in real values
streamlit run main.py
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

### Weekly shopping run

1. Open the app URL, log in.
2. Paste your grocery list into the text area and click **Parse List**.
3. Review the structured list, optionally add staples, then click **Looks Good — Find Products**.
4. If the Sale Scan screen appears, review on-sale alternatives and decide whether to switch.
5. Work through the review queue one item at a time:
   - Read the product card
   - Swap to an alternative if you prefer it
   - Check "Save as my preferred choice" to remember this product
   - Adjust quantity if needed
   - Click **Add to Cart** to confirm and advance
6. After the last item, SmartCart posts everything to your Kroger cart.
7. Click **Open City Market Cart** → pick pickup time → confirm payment → place order.

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
| `main.py` | Streamlit UI — all screens, navigation, OAuth callback handler |
| `list_parser.py` | Parses raw grocery list text via Claude API |
| `product_matcher.py` | Matches items to Kroger products (parallel; 5 workers) |
| `sale_scanner.py` | Scans for on-sale alternatives (parallel; 5 workers) |
| `cart_manager.py` | Posts confirmed items to Kroger cart |
| `preference_store.py` | Single source of truth for persistent data — Supabase-backed |
| `kroger_auth.py` | Kroger OAuth 2.0 + PKCE (hosted-friendly) |
| `supabase_kv.py` | Tiny key-value layer over the Supabase `kv` table |
| `requirements.txt` | Python dependencies |
| `.env.example` | Local config template |
| `.streamlit/secrets.toml.example` | Streamlit Cloud secrets template |

---

## 6. Troubleshooting

### "Connect Kroger" loops back without authorizing
- The redirect URI registered in the Kroger developer portal doesn't exactly match `KROGER_REDIRECT_URI`. Both must include the trailing slash and use the same scheme (`https://` on the cloud, `http://` locally).

### Preferences/staples disappeared
- Almost certainly a Supabase config issue — `SUPABASE_URL` or `SUPABASE_SERVICE_KEY` is wrong or pointing at a different project. Restore from a `smartcart_backup_*.json` snapshot via Preferences → Backup & Restore.

### Streamlit Cloud "Secrets not found" or import errors
- Open the Streamlit Cloud dashboard → your app → **Manage app → Logs**. Most failures show up there.

### Kroger token expired and won't refresh
- From the home screen, click **Connect Kroger** to re-authorize. Refresh tokens live for ~6 months; re-auth is fast.

### No locations found when searching for store
- Kroger's locations API can be finicky with city names. Try a zip code instead.

### Items posting to cart but not appearing in City Market
- Kroger cart sync can take 30-60 seconds. Refresh the City Market cart page. If items still don't appear, use **Retry Failed Items** on the session summary screen.

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
| Supabase free tier | Free | 500 MB Postgres — we use < 1 MB |
| Kroger Public API | Free | Builder Tier, 500k credits/month, SmartCart uses < 1k |
| Claude Haiku 4.5 | ~$1-3 | ~15-25 items × ~6 sessions/month |
| **Total** | **~$1-3/month** | Set Anthropic spending cap to $10 as a ceiling |

---

*SmartCart v2.0 — Streamlit Cloud + Supabase. For household use. Not for distribution.*
