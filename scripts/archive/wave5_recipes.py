"""
wave5_recipes.py — Wave 5 (2026-10-18): fill the two gaps the cooked-vs-
passed-over review found. Fish 3 (only Sticky Miso Salmon has earned its slot;
the plain lemon/tahini/breaded fish keep getting swapped out, so these lean
glazed + Asian) and takeout-style Chinese 2 (what gets picked on a swap).

Same bar as prior waves: serves 4, grouped ingredients, mild, store-bought
sauces where one exists, a true cooked vegetable in every recipe, no honey.
source_url left blank — these are house versions of classics, not adapted
from one citable page. All enter unproven (times_cooked 0).

Apply: PYTHONPATH=. python3 scripts/archive/apply_wave.py scripts/archive/wave5_recipes.py [--apply]
"""


def _i(name, amount, unit, aisle, group, text):
    return {"name": name, "amount": amount, "unit": unit, "aisle": aisle,
            "group": group, "original_text": text}


def _s(*texts):
    return [{"step_number": n, "text": t} for n, t in enumerate(texts, 1)]


def _recipe(id, title, cuisine, protein, carb, equipment, prep, cook, ingredients, steps):
    return {
        "id": id, "title": title, "source": "claude_chat", "source_url": "", "image_url": "",
        "servings_original": 4, "prep_minutes": prep, "cook_minutes": cook,
        "ready_in_minutes": prep + cook, "cuisines": [cuisine], "diet_tags": [],
        "dish_types": ["main course"], "equipment": equipment,
        "proteins": [protein], "carbs": [carb], "status": "active",
        "regen_pending": False, "ingredients": ingredients, "instructions": steps,
    }


WAVE = [
    # ---------------- FISH ----------------
    _recipe(
        "gen_teriyaki_salmon_bowls", "Air-Fryer Teriyaki Salmon Bowls", "japanese", "fish", "rice",
        ["air_fryer", "stovetop"], 10, 20,
        [
            _i("salmon fillets (6 oz)", 4, "count", "Seafood", "Salmon", "4 (6-oz) salmon fillets, skin off"),
            _i("teriyaki sauce", 0.5, "cup", "Ethnic Foods", "Salmon", "1/2 cup store-bought teriyaki sauce, divided"),
            _i("sesame seeds", 1, "tbsp", "Ethnic Foods", "Salmon", "1 tbsp sesame seeds"),
            _i("broccoli florets", 1.5, "lb", "Produce", "Vegetables", "1.5 lb broccoli florets"),
            _i("vegetable oil", 1, "tbsp", "Oil, Vinegar, Salad Dressing", "Vegetables", "1 tbsp vegetable oil"),
            _i("kosher salt", 0.5, "tsp", "Spices and Seasonings", "Vegetables", "1/2 tsp kosher salt"),
            _i("jasmine rice", 1.5, "cup", "Pasta and Rice", "Bowls", "1.5 cups jasmine rice (uncooked)"),
            _i("cucumber", 1, "count", "Produce", "Bowls", "1 cucumber, sliced"),
            _i("avocado", 1, "count", "Produce", "Bowls", "1 avocado, sliced"),
            _i("scallions", 1, "bunch", "Produce", "Bowls", "1 bunch scallions, sliced"),
        ],
        _s(
            "Start the rice.",
            "Toss the broccoli with oil and salt. Air-fry at 400°F for 8-10 minutes, shaking once, until the edges char. Tip into a bowl and keep warm.",
            "Brush the salmon with half the teriyaki sauce. Air-fry at 400°F for 7-9 minutes, brushing with more sauce halfway, until it flakes and the glaze is sticky. No flip needed.",
            "Warm the remaining teriyaki in a small pan 1 minute to drizzle.",
            "Build bowls: rice, salmon, broccoli, cucumber and avocado. Drizzle with the warm sauce and finish with sesame seeds and scallions.",
        ),
    ),
    _recipe(
        "gen_sweet_chili_salmon_noodles", "Sweet Chili Salmon with Snap Peas & Rice Noodles", "thai", "fish", "pasta",
        ["air_fryer", "skillet", "stovetop"], 15, 15,
        [
            _i("salmon fillets (6 oz)", 4, "count", "Seafood", "Salmon", "4 (6-oz) salmon fillets, skin off"),
            _i("sweet chili sauce", 0.5, "cup", "Ethnic Foods", "Salmon", "1/2 cup store-bought Thai sweet chili sauce, divided"),
            _i("soy sauce", 2, "tbsp", "Ethnic Foods", "Salmon", "2 tbsp soy sauce, divided"),
            _i("lime", 2, "count", "Produce", "Salmon", "2 limes (1 juiced, 1 in wedges)"),
            _i("rice noodles (8 oz)", 1, "package", "Ethnic Foods", "Noodles", "1 (8-oz) package flat rice noodles"),
            _i("toasted sesame oil", 1, "tsp", "Ethnic Foods", "Noodles", "1 tsp toasted sesame oil"),
            _i("sugar snap peas", 12, "oz", "Produce", "Vegetables", "12 oz sugar snap peas, trimmed"),
            _i("red bell pepper", 1, "count", "Produce", "Vegetables", "1 red bell pepper, thinly sliced"),
            _i("carrots", 2, "count", "Produce", "Vegetables", "2 carrots, cut into thin matchsticks"),
            _i("vegetable oil", 1, "tbsp", "Oil, Vinegar, Salad Dressing", "Vegetables", "1 tbsp vegetable oil"),
            _i("garlic", 2, "clove", "Produce", "Vegetables", "2 garlic cloves, minced"),
            _i("fresh cilantro", 0.25, "cup", "Produce", "For serving", "1/4 cup cilantro leaves"),
        ],
        _s(
            "Soak the rice noodles in hot tap water per the package (about 8 minutes), drain, and toss with the sesame oil.",
            "Stir 1/4 cup sweet chili sauce with 1 tbsp soy and the juice of 1 lime. Brush half over the salmon; save the rest.",
            "Air-fry the salmon at 400°F for 7-9 minutes until it flakes, brushing with the reserved glaze halfway.",
            "Meanwhile heat the oil in a large skillet over medium-high. Stir-fry the carrots 2 minutes, then the snap peas and bell pepper 3 minutes until crisp-tender. Add the garlic for 30 seconds.",
            "Add the noodles, the remaining 1/4 cup sweet chili sauce and 1 tbsp soy; toss 1-2 minutes until glossy and hot.",
            "Serve the salmon over the noodles and vegetables with cilantro and lime wedges.",
        ),
    ),
    _recipe(
        "gen_salmon_fried_rice", "Salmon Fried Rice", "chinese", "fish", "rice",
        ["air_fryer", "wok", "stovetop"], 15, 20,
        [
            _i("salmon fillets (6 oz)", 3, "count", "Seafood", "Salmon", "3 (6-oz) salmon fillets, skin off"),
            _i("soy sauce", 3, "tbsp", "Ethnic Foods", "Salmon", "3 tbsp soy sauce, divided"),
            _i("jasmine rice", 1.5, "cup", "Pasta and Rice", "Rice", "1.5 cups jasmine rice, cooked and chilled (or 4 cups day-old rice)"),
            _i("eggs", 3, "count", "Eggs", "Rice", "3 eggs, beaten"),
            _i("vegetable oil", 2, "tbsp", "Oil, Vinegar, Salad Dressing", "Rice", "2 tbsp vegetable oil, divided"),
            _i("oyster sauce", 1, "tbsp", "Ethnic Foods", "Rice", "1 tbsp oyster sauce"),
            _i("toasted sesame oil", 1, "tsp", "Ethnic Foods", "Rice", "1 tsp toasted sesame oil"),
            _i("frozen peas and carrots", 2, "cup", "Frozen", "Vegetables", "2 cups frozen peas and carrots"),
            _i("broccoli florets", 12, "oz", "Produce", "Vegetables", "12 oz broccoli florets, chopped small"),
            _i("garlic", 3, "clove", "Produce", "Vegetables", "3 garlic cloves, minced"),
            _i("scallions", 1, "bunch", "Produce", "Vegetables", "1 bunch scallions, sliced (whites and greens apart)"),
        ],
        _s(
            "Cook the rice early (or use day-old) and spread it on a tray to cool — warm rice turns gummy.",
            "Brush the salmon with 1 tbsp soy. Air-fry at 400°F for 8-9 minutes until it flakes, then break into large flakes.",
            "Heat 1 tbsp oil in a wok over high. Scramble the eggs until just set; scoop out.",
            "Add the remaining oil, the broccoli and scallion whites; stir-fry 3 minutes. Add the peas and carrots and garlic for 2 minutes.",
            "Add the rice, pressing it against the wok, and stir-fry 3-4 minutes until hot and a little crisp. Add the remaining 2 tbsp soy and the oyster sauce.",
            "Fold in the eggs and salmon gently so the flakes stay big. Off the heat, add the sesame oil and scallion greens.",
        ),
    ),

    # ---------------- TAKEOUT-STYLE CHINESE ----------------
    _recipe(
        "gen_air_fryer_orange_chicken", "Air-Fryer Orange Chicken with Broccoli", "chinese", "chicken", "rice",
        ["air_fryer", "sauce_pan", "stovetop"], 15, 20,
        [
            _i("boneless skinless chicken thighs", 1.75, "lb", "Meat", "Chicken", "1.75 lb boneless skinless chicken thighs, 1-inch pieces"),
            _i("cornstarch", 0.33, "cup", "Baking", "Chicken", "1/3 cup cornstarch"),
            _i("eggs", 1, "count", "Eggs", "Chicken", "1 egg, beaten"),
            _i("kosher salt", 0.5, "tsp", "Spices and Seasonings", "Chicken", "1/2 tsp kosher salt"),
            _i("orange sauce", 0.75, "cup", "Ethnic Foods", "Sauce", "3/4 cup store-bought orange sauce (e.g., Panda Express)"),
            _i("orange", 1, "count", "Produce", "Sauce", "1 orange, zested"),
            _i("broccoli florets", 1.5, "lb", "Produce", "Vegetables", "1.5 lb broccoli florets"),
            _i("vegetable oil", 2, "tbsp", "Oil, Vinegar, Salad Dressing", "Vegetables", "2 tbsp vegetable oil, divided (basket + broccoli)"),
            _i("jasmine rice", 1.5, "cup", "Pasta and Rice", "For serving", "1.5 cups jasmine rice (uncooked)"),
            _i("scallions", 1, "bunch", "Produce", "For serving", "1 bunch scallions, sliced"),
        ],
        _s(
            "Start the rice.",
            "Toss the chicken with the egg and salt, then with the cornstarch until every piece is dry-coated.",
            "Brush the air-fryer basket with oil, add the chicken in one layer (batches if needed), and brush or mist the tops with a little oil. Air-fry at 400°F for 12-14 minutes, shaking halfway, until crisp and 175°F.",
            "Toss the broccoli with the remaining oil and a pinch of salt; air-fry at 400°F for 8 minutes, shaking once (or steam it while the chicken cooks).",
            "Warm the orange sauce with the zest in a small saucepan until bubbling. Toss the hot chicken in the sauce right before serving so it stays crisp.",
            "Serve over rice with the broccoli and scallions.",
        ),
    ),
    _recipe(
        "gen_chicken_lo_mein", "Chicken Lo Mein", "chinese", "chicken", "pasta",
        ["wok", "stovetop"], 15, 15,
        [
            _i("lo mein noodles (16 oz)", 1, "package", "Ethnic Foods", "Noodles", "1 (16-oz) package lo mein noodles (or spaghetti)"),
            _i("boneless skinless chicken thighs", 1.25, "lb", "Meat", "Chicken", "1.25 lb boneless skinless chicken thighs, thinly sliced"),
            _i("cornstarch", 1, "tsp", "Baking", "Chicken", "1 tsp cornstarch"),
            _i("soy sauce", 4, "tbsp", "Ethnic Foods", "Sauce", "4 tbsp soy sauce, divided"),
            _i("oyster sauce", 3, "tbsp", "Ethnic Foods", "Sauce", "3 tbsp oyster sauce"),
            _i("toasted sesame oil", 2, "tsp", "Ethnic Foods", "Sauce", "2 tsp toasted sesame oil"),
            _i("sugar", 1, "tsp", "Baking", "Sauce", "1 tsp sugar"),
            _i("vegetable oil", 2, "tbsp", "Oil, Vinegar, Salad Dressing", "Vegetables", "2 tbsp vegetable oil, divided"),
            _i("cremini mushrooms", 8, "oz", "Produce", "Vegetables", "8 oz cremini mushrooms, sliced"),
            _i("carrots", 2, "count", "Produce", "Vegetables", "2 carrots, cut into matchsticks"),
            _i("red bell pepper", 1, "count", "Produce", "Vegetables", "1 red bell pepper, thinly sliced"),
            _i("baby bok choy", 1, "lb", "Produce", "Vegetables", "1 lb baby bok choy, sliced 1 inch (stems and leaves apart)"),
            _i("garlic", 3, "clove", "Produce", "Vegetables", "3 garlic cloves, minced"),
            _i("fresh ginger", 1, "tbsp", "Produce", "Vegetables", "1 tbsp grated ginger"),
        ],
        _s(
            "Cook the noodles per the package, drain, rinse briefly, and toss with 1 tsp sesame oil.",
            "Toss the chicken with 1 tbsp soy and the cornstarch. Stir together the remaining 3 tbsp soy, oyster sauce, remaining sesame oil, sugar and 2 tbsp water.",
            "Heat 1 tbsp oil in a wok over high. Sear the chicken in one layer 2 minutes, then stir-fry 2-3 minutes until cooked through; scoop out.",
            "Add the remaining oil. Stir-fry the mushrooms 3 minutes until browned, then the carrots, bell pepper and bok choy stems 2 minutes. Add the bok choy leaves, garlic and ginger for 1 minute.",
            "Return the chicken, add the noodles and sauce, and toss with tongs 1-2 minutes until glossy and hot.",
        ),
    ),
]
