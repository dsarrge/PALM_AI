from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from food_ai.ui.services.meal_log_service import list_recent_meal_logs, save_meal_log


class MealLogServiceTests(unittest.TestCase):
    def test_save_meal_log_creates_schema_and_scales_nutrition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "palm_test.db"
            formatted = {
                "summary": {
                    "top_dish": "lasagna",
                    "overall_confidence": "82.5%",
                },
                "nutrition": {
                    "summary": {
                        "calories": 180,
                        "protein_g": 9,
                        "fat_g": 8,
                        "carbs_g": 18,
                        "fiber_g": 2,
                        "total_sugars_g": 4,
                        "sodium_mg": 420,
                        "cholesterol_mg": 25,
                    }
                },
                "ingredients": {
                    "ranked": [
                        {"ingredient": "lasagna noodles", "include_in_nutrition": True},
                        {"ingredient": "ricotta cheese", "include_in_nutrition": True},
                    ]
                },
            }

            record = save_meal_log(
                image_path="meal.jpg",
                inference_result={"dish_fused_topk": [{"name": "lasagna", "score": 0.825}]},
                formatted_result=formatted,
                serving_weight_g=250,
                notes="dinner",
                db_path=db_path,
            )

            self.assertTrue(db_path.exists())
            self.assertEqual(record.dish_name, "lasagna")
            self.assertEqual(record.calories, 450.0)

            rows = list_recent_meal_logs(db_path=db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["dish_name"], "lasagna")
            self.assertEqual(rows[0]["calories"], 450.0)

            with sqlite3.connect(db_path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(meal_logs)").fetchall()
                }
            self.assertIn("inference_result_json", columns)
            self.assertIn("included_ingredients_json", columns)


if __name__ == "__main__":
    unittest.main()
