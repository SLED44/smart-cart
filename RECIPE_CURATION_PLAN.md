# Recipe Library Curation Plan

*2026-06-12. Status: **APPROVED with amendments** — miso salmon KEEP, Chicken Katsu KEEP, banh mi REBUILD. Honey clarification: not banned, just keep the frequency low (the seed session's "honey near-zero" hard rule was an over-encoding).*

## Why we're doing this

The product review found the meal-plan frustrations are a **content supply problem**: 67 recipes scraped from Spoonacular (junky data, random-blog quality, servings 1–30) + 44 bulk-generated in waves optimized for cuisine quotas (schema-perfect, terse 4-step instructions, never taste-tested — the inedible tofu bowl was one). 18 recipes (16%) have already been never-again'd; week-2 planning took 4 swaps + 2 rejections before confirm.

**Principles for the new library:**

1. **Curation over quotas.** ~55–65 recipes the family actually wants beats 150 of filler. The old "expand to 156 so every cuisine has the same repeat interval" plan is dead.
2. **Provenance.** New/regenerated recipes get adapted from a real, proven source (NYT Cooking, Serious Eats, Budget Bytes, your pastes) — not invented from nothing. The paste-recipe prompt now enforces the quality bar (ingredient groups, one unit per ingredient, per-step quantities, temps, doneness cues, 6–10 substantive steps).
3. **Probation.** Every regenerated/new recipe starts unproven. It earns its rotation slot the first time it's cooked with a "Made it." (The scoring feedback loop shipped today already demotes swap-rejects and promotes cooked-as-written.)
4. **House rules are hard constraints**: mild (kids), air-fryer-friendly where sensible, store-bought sauces, lamb = ground only, serves 4 *at import* (no more 0.67-lb scaling artifacts). Honey/sweet glazes are fine — just at low frequency, not as the library's default flavor base.

Signals used below: cooked outcomes + user notes, swap/never-again history, favorites, data quality (junk ingredient rows, servings far from 4, instruction depth), and house-rule fit.

---

## ✅ KEEP — proven or solid as-is (16)

| Recipe | Why |
|---|---|
| Greek Turkey Burgers with Tzatziki (`gen_greek_turkey_burgers`) | Cooked, you saved notes — apply notes at next regen pass |
| Turkey Taco Rice Skillet (`gen_turkey_taco_skillet`) | Cooked, notes saved. Instructions are 3 steps — worth a polish, but it earned its slot |
| Air-Fryer Meatball Subs (`nyt_meatball_subs`) | Cooked, "Made it" — first fully clean outcome in the log |
| Baked Honey Sriracha Chicken Wings (`sp_633651`) | Cooked with notes. Honey exception — you picked it yourself, twice |
| Spiced Lamb Meatballs w/ Lemon Mint Yogurt (`lib_greek_lamb_meatballs`) | Your one favorite; ground lamb (fits lamb rule). Instructions are 3 steps → light polish only |
| Pho With Zucchini Noodles (`sp_1096250`) | Explicitly protected by you during the seed session |
| Bean and Cheese Burritos (`nyt_bean_cheese_burritos`) | Your NYT pick, clean data |
| Chicken Enchiladas (`nyt_chicken_enchiladas`) | Your NYT pick (you cut all 4 *scraped* enchilada recipes but kept this one — telling) |
| Air-Fryer Chicken Katsu (`nyt_chicken_katsu`) | Your NYT pick — confirmed keep. The new scoring stops re-proposing it after the 2× swap-outs; it comes back when the penalty window lapses |
| Sticky Miso Salmon Bowls (`nyt_miso_salmon_bowls`) | Confirmed keep (honey OK at low frequency) |
| Grilled Honey-Mustard Chicken Thighs (`nyt_honey_mustard_thighs`) | Rescued from cut — honey isn't banned, and it's your NYT pick |
| Chicken Piccata (`nyt_chicken_piccata`) | Your NYT pick, best instructions of the gen batch |
| Chicken Panang Curry (`nyt_panang_curry`) | Your NYT pick |
| Quick Pasta e Fagioli (`nyt_pasta_e_fagioli`) | Your NYT pick |
| White Chicken Chili (`nyt_white_chicken_chili`) | Your NYT pick |
| Chinese Style Chicken and Noodle Stir Fry (`sp_638714`) | In this week's plan; cleanest of the scraped Chinese recipes |

## 🔄 REGENERATE — right concept, rebuild the content (~38)

Same dish, rebuilt from a real source with the new schema (grouped ingredients, serves-4 at import, real instructions, house rules applied). Roughly in priority order — top section first since they're in active rotation.

**Wave 1 — in current/recent plans (5):**

| Recipe | Notes |
|---|---|
| Slow-Cooker Moroccan Chicken Tagine (`gen_moroccan_chicken_tagine`) | In this week's plan — regen before you cook it |
| Beef & Broccoli (`gen_chinese_beef_broccoli`) | Classic kid-friendly; 4 terse steps now |
| Kung Pao Chicken (`gen_chinese_kung_pao_chicken`) | Verify mild-at-table treatment |
| Quick Egg Fried Rice (`gen_chinese_egg_fried_rice`) | Good weeknight filler |
| Greek Lemon-Oregano Chicken & Potatoes (`gen_greek_lemon_chicken_potatoes`) | You swapped it out once — regen, then let scoring decide |

**Wave 2 — generated concepts worth saving (23):**
`gen_beef_kofta_rice`, `gen_black_bean_quesadillas`, `gen_coconut_chickpea_curry`, `gen_greek_shrimp_saganaki`, `gen_harissa_chicken_chickpeas`, `gen_indian_chicken_korma`, `gen_indian_paneer_tikka`, `gen_me_beef_arayes`, `gen_me_chicken_shawarma_bowls`, `gen_me_chickpea_shakshuka`, `gen_me_tahini_salmon`, `gen_moroccan_beef_lentil_soup`, `gen_moroccan_chickpea_stew`, `gen_moroccan_lamb_couscous`, `gen_moroccan_shrimp_chermoula`, `gen_pesto_chicken_skillet`, `gen_pork_carnitas_tacos`, `gen_pork_chops_sheet_pan`, `gen_pork_tenderloin_slaw`, `gen_sausage_peppers_sheet_pan`, `gen_viet_ginger_pork_bowls`, `gen_viet_lemongrass_chicken`, `gen_viet_shaking_beef`

**Wave 3 — scraped recipes with good bones but junky data (11):**

| Recipe | Problem to fix at regen |
|---|---|
| Greek Lemon Chicken Orzo Soup (`sp_1098350`) | Serves 8 → 4 |
| Beef Teriyaki Stir Fry (`sp_634710`) | Serves 2 → 4 |
| Easy Chicken Tikka Masala (`sp_641908`) | 2 junk rows; otherwise solid |
| Indian Lentil Dahl (`sp_647830`) | Light cleanup |
| Korean Beef Rice Bowl (`sp_649030`) | 14 rambling steps → tighten |
| Korean Chicken Stew (`sp_649040`) | 213-char instructions — too thin |
| Grilled Chicken Banh Mi (`sp_645634`) | 12 steps, serves 6 → 4 — rebuild confirmed |
| Asian Shrimp Stir-Fry (`sp_631748`) | Solid; minor cleanup |
| Asian Salmon Burgers w/ Ginger Lime Sauce (`sp_632874`) | From-scratch sauce → store-bought swap |
| Japanese Mabo Tofu With Eggplant (`sp_648479`) | Tofu missing from its own ingredient list(!); to-ban-jan heat vs mild kids |
| Turkey Pot Pie (`sp_715467`) | Serves 8 → 4; good comfort-food slot |

## ❌ CUT — wrong fit, junk data, or rejected concept (38)

Set `status=retired` (they stay in the KV blob and can be restored — nothing is deleted).

**House-rule conflicts:**

| Recipe | Reason |
|---|---|
| Sheet-Pan Gochujang Chicken (`gen_gochujang_chicken`) | Gochujang heat vs mild kids (honey no longer the reason) |
| Air-Fryer Lemon-Herb Salmon & Green Beans (`gen_lemon_herb_salmon`) | You swapped it out; fish slots are scarce (1/wk) — keep only proven fish |
| Moroccan-Spiced Turkey Meatballs (`gen_moroccan_turkey_meatballs`) | Swapped out; Greek turkey burgers + taco skillet already own the turkey slots |

**Cheffy / fussy / not a family-of-4 weeknight (violates simple-and-mild):**
`sp_636599` Butternut Gnocchi w/ Whiskey Cream (22 steps, from-scratch gnocchi) · `sp_654072` Filet Mignon on Kataifi (serves 2, 13 steps) · `sp_658967` Saffron Chicken Tikka (19 steps) · `sp_660493` Soba in Kombu Dashi (serves 2, 15 steps) · `sp_639606` Classic Greek Moussaka (90-min build) · `sp_640266` Crab & Shrimp Burgers w/ Garlic Grits Fries (serves 8) · `sp_663177` Thai-Style Mussels (kids + mussels) · `sp_657939` Ratatouille With Brie · `sp_644135` Galbi Tang (short-rib soup, long braise)

**Serves-2 blog recipes / sides masquerading as mains:**
`sp_663126` Thai Pasta Salad · `sp_663149` Thai Sausage Salad · `sp_716311` Mango Fried Rice · `sp_715769` Broccolini Quinoa Pilaf · `sp_664835` Vietnamese Noodle Salad w/ Tofu ("garlic gloves", 2 steps)

**Data too junky to rebuild:**
`sp_637697` Chelley's Thai Satay (serves **30**, 9 junk rows) · `sp_638642` Chinese Chicken Salad w/ Chipotle ("pea-mond dressing" header leaked in as an ingredient, 4 junk rows) · `sp_716408` Greek-Style Baked Fish (3 junk rows) · `sp_645384` Greek Yogurt Chicken Salad (2 junk rows, mayo-salad ≠ dinner)

**Concept you've already rejected (sibling recipes never-again'd):**
`sp_634965` Bibimbab — you never-again'd the other bibimbab; this is a near-duplicate · `sp_664025` Turkey Enchilada Bake — you've cut four enchilada recipes (your NYT one survives) · `sp_649036` Korean Candy Chicken — junky scraped sugar-glaze; cut keeps glaze frequency low · `sp_638369` Korean Sweet n Sour Chicken — same

**Marginal / never picked, low signal:**
`sp_641565` Donkatsu · `sp_641893` Easy Cheesy Pizza Casserole · `sp_642540` Falafel Burgers · `sp_645265` Great Greek Salad (side) · `sp_648462` Japanese Clear Soup (side) · `sp_663033` Teriyaki Flank Steak Sandwich · `sp_663156` Thai Cashew Chicken · `sp_664830` Vietnamese Beef-Noodle Soup · `sp_715573` Simple Skillet Lasagna · `sp_716300` Plantain Pizza · `sp_716344` Kenyan Pilau · `sp_716364` Coconut Curry Mackerel w/ Rice & Peas · `sp_776505` Sausage & Pepperoni Stromboli

---

## End state & gaps

16 keep + 39 regenerated = **55 active recipes**, all serves-4, grouped ingredients, real instructions. Gaps to fill with *new* sourced recipes: **american and italian thin out after cuts** (the anchors that must appear weekly) — suggest 4–6 new sourced recipes each there, picked together from NYT/Serious Eats; plus 1–2 proven fish dishes since the salmon cuts leave fish light.

> **EXECUTED 2026-06-12.** Dispositions applied (38 retired, 55 active) and all
> three regeneration waves are live — `wave1_recipes.py` (5), `wave2a/2b_recipes.py`
> (23), `wave3_recipes.py` (11). `regen_pending` = 0. 45/55 active recipes carry
> source_url provenance; 49/55 are serves-4 (the exceptions are untouched KEEPs).
> Remaining: anchor-cuisine additions (american/italian) + proven fish — pick
> with the user.

## Execution plan

1. **Apply dispositions** — one script, backup first: cuts → `status=retired` (new status; planner + swap pools exclude it). Regen-list recipes **stay active** with a `regen_pending` flag — pulling 39 recipes from rotation before replacements exist would starve the planner; each regenerated recipe overwrites in place (same id, cooked-history preserved by `library.save`'s merge).
2. **Regenerate Wave 1** (5 in-rotation recipes) via the upgraded schema — each adapted from a named real source, validated, saved with `source_url` provenance. You taste-test through normal weekly use.
3. **Waves 2–3** at ~10/week so cooked-outcome results inform the next wave.
4. **Anchor-cuisine additions** — picked together, not auto-generated.

**Resolved 2026-06-12:** miso salmon KEEP · Chicken Katsu KEEP · banh mi REBUILD · honey = low-frequency preference, not a ban.
