# Archived one-shot scripts

One-shot recipe-curation migration scripts from June 2026. Each `waveN_recipes.py`
file holds a batch of curated recipe data, and `apply_wave.py` / `apply_curation.py`
pushed those batches into the Supabase recipe library. They were run once per wave
and are kept only for reference — see `RECIPE_CURATION_PLAN.md` at the repo root
for the plan they implemented.

Do not re-run: they would overwrite recipes that have since been edited in the
live library.
