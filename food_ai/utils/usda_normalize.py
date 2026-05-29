from __future__ import annotations


def contains_phrase(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def normalize_usda_ingredient(text: str) -> str | None:
    lowered = text.lower().strip()
    explicit = {
        "cheese, asiago": "asiago cheese",
        "milk, almond, unsweetened": "almond milk",
        "sauce, alfredo mix, dry": "alfredo sauce",
        "bread, wheat": "wheat bread",
        "sugars, brown": "brown sugar",
        "sauce, worcestershire": "worcestershire sauce",
        "leavening agents, baking powder, low-sodium": "baking powder",
        "spices, bay leaf": "bay leaf",
        "spices, cinnamon, ground": "cinnamon",
        "cocoa, dry powder, unsweetened": "cocoa powder",
        "vinegar, red wine": "vinegar",
        "nuts, walnuts, english": "walnut",
        "peppers, sweet, green, raw": "bell pepper",
        "margarine, regular, hard, soybean (hydrogenated)": "margarine",
        "tomatoes, red, ripe, raw, year round average": "tomato",
        "beef, ground, 95% lean meat / 5% fat, raw": "beef ground",
        "mayonnaise, made with tofu": "mayonnaise",
        'beef, flank, steak, separable lean only, trimmed to 0" fat, choice, raw': "beef flank steak",
        "pork, fresh, loin, tenderloin, separable lean only, raw": "pork tenderloin",
        "chicken, wing, meat and skin, raw": "chicken wing",
        "duck, domesticated, liver, raw": "duck liver",
        "turkey, whole, giblets, raw": "turkey giblet",
        "soymilk, original and vanilla, unfortified": "soy milk",
        "chicken, broilers or fryers, leg, meat and skin, raw": "chicken leg",
        "soup, stock, chicken, home-prepared": "chicken stock",
        "fish, snapper, mixed species, raw": "snapper",
        "apricot nectar, canned, with added ascorbic acid": "apricot juice",
        "lemons, raw, without peel": "lemon",
        "oranges, raw, navels": "orange",
        "peas, green, raw": "pea",
        "bread, potato": "potato bread",
        "cheese, cream, low fat": "cream cheese",
        "milk, buttermilk, fluid, cultured, lowfat": "buttermilk",
        "milk, sheep, fluid": "sheep milk",
        "rice, white, long-grain, regular, raw, enriched": "rice white",
        "rice flour, white, unenriched": "rice flour",
        "fish oil, sardine": "sardine oil",
        "oil, mustard": "mustard oil",
        "mustard, prepared, yellow": "mustard",
        "bagels, wheat": "bagel",
        "peach nectar, canned, with added ascorbic acid": "peach nectar",
        "peppers, sweet, red, sauteed": "cooked bell pepper",
        "peas, green, cooked, boiled, drained, without salt": "cooked pea",
    }
    if lowered in explicit:
        return explicit[lowered]
    if lowered in {"pork, pickled pork hocks", "chicken, canned, no broth"}:
        return None
    return None
