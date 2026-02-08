# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 13:04:38 2025

@author: dsarr

Food AI Orchestrator
run_food_ai.py

Spyder:
%runfile C:/PALM/food_ai/run_food_ai.py --args C:/PALM/tests/food_1.jpg
"""
import sys
sys.path.insert(0, "C:/PALM")

import warnings
warnings.filterwarnings("ignore")

import json
from food_ai.infer.meal_infer import meal_infer


def run(image_path: str) -> dict:
    """
    End-to-end Food AI pipeline.
    Image → Dish → Ingredients → Nutrition → DB log
    """
    result = meal_infer(image_path)
    return result

# ENTRY (CLI / debug)
if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise ValueError("Usage: python run_food_ai.py <image_path>")

    img = sys.argv[1]
    out = run(img)
    print(json.dumps(out, indent=2))

# # -*- coding: utf-8 -*-
# """
# Created on Mon Dec 29 13:04:38 2025

# @author: dsarr

# Food AI Orchestrator
# run_food_ai.py

# %runfile C:/PALM/food_ai/run_food_ai.py --args C:/PALM/tests/food_8.jpg

# """
# import sys
# sys.path.insert(0, "C:/PALM")

# import warnings
# warnings.filterwarnings("ignore")

# from pathlib import Path
# import json
# import numpy as np

# from sentence_transformers import SentenceTransformer

# from food_ai.infer.infer_food import infer_dish
# from food_ai.infer.ingredient_graph import ingredient_graph
# from food_ai.infer.nutrition_infer import infer_nutrition
# from food_ai.sync.log_meal import log_meal

# # PATHS
# BASE_DIR = Path(__file__).resolve().parents[1]   # C:/PALM
# ARTIFACTS = BASE_DIR / "artifacts"

# # CONFIG
# TOP_K = 10
# MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# # LOAD MODELS / DATA
# model = SentenceTransformer(MODEL_NAME)

# recipe_embeddings = np.load(ARTIFACTS / "recipe_embeddings.npy")
# with open(ARTIFACTS / "recipe_ids.json", "r", encoding="utf-8") as f:
#     recipe_ids = json.load(f)

# # CORE PIPELINE
# def run(image_path: str) -> dict:
#     """
#     End-to-end Food AI pipeline.

#     Returns:
#     {
#         "dish": str,
#         "confidence": float,
#         "nutrition_path": Path
#     }
#     """
#     # STEP 1: Image → Dish
#     vision = infer_dish(image_path)
#     dish_name = vision["dish"]
#     confidence = vision["confidence"]

#     dish_key = dish_name.lower().replace(" ", "_")
#     graph_path = ARTIFACTS / f"ingredient_graph_{dish_key}.json"

#     # STEP 2: Dish → Recipe Match
#     dish_embedding = model.encode(
#         dish_name,
#         convert_to_numpy=True,
#         normalize_embeddings=True
#     )

#     scores = recipe_embeddings @ dish_embedding
#     top_idx = np.argsort(scores)[-TOP_K:][::-1]

#     top_recipe_ids = [recipe_ids[i] for i in top_idx]
#     top_scores = scores[top_idx].tolist()

#     # STEP 3: Recipe → Ingredient Graph
#     graph = ingredient_graph(top_recipe_ids, top_scores)

#     graph_path.parent.mkdir(exist_ok=True)
#     with open(graph_path, "w", encoding="utf-8") as f:
#         json.dump(graph, f, indent=2)

#     # STEP 4: Ingredients → Nutrition
#     nutrition = infer_nutrition(graph, dish_key=dish_key)
#     meal_id = log_meal(
#         upload_method="image",
#         inferred_dish=dish_name,
#         model_confidence=confidence,
#         final_dish=dish_name,      # no user correction yet
#         ingredients=graph,
#         nutrition=nutrition["nutrition"],
#         image_path=image_path,
#         barcode=None,
#         model_version="v1"
#     )

#     nutrition_path = ARTIFACTS / f"nutrition_estimate_{dish_key}.json"

#     return {
#         "dish": dish_name,
#         "confidence": confidence,
#         "meal_id": meal_id,
#         "nutrition_path": str(nutrition_path)
#         }

# # ENTRY
# if __name__ == "__main__":
#     import sys

#     if len(sys.argv) < 2:
#         raise ValueError("Usage: python run_food_ai.py <image_path>")

#     result = run(sys.argv[1])
#     print(json.dumps(result, indent=2))
