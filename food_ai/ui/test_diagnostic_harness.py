from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_APP_TEST_TEMP = Path('C:/PALM/artifacts/apptest_tmp')
_APP_TEST_TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_APP_TEST_TEMP)
os.environ['TEMP'] = str(_APP_TEST_TEMP)
os.environ['TMP'] = str(_APP_TEST_TEMP)

from streamlit.testing.v1 import AppTest

from food_ai.ui.config import UISettings
from food_ai.ui.services.inference_service import UIInferenceOptions, run_food_inference
from food_ai.ui.services.sample_cases import get_sample_image_cases
from food_ai.vision.types import DishPrediction, PredictionResult


class FoodVisionTesterHarnessTests(unittest.TestCase):
    def test_sample_case_catalog_includes_non_food_and_difficult_cases(self) -> None:
        cases = get_sample_image_cases(UISettings())
        self.assertGreaterEqual(len(cases), 6)
        categories = {case.category for case in cases}
        self.assertIn('non_food', categories)
        self.assertIn('difficult_food', categories)
        self.assertIn('packaging_only', categories)
        self.assertTrue(all(case.path.exists() for case in cases))

    def test_streamlit_app_renders_and_loads_a_sample_case(self) -> None:
        app_path = Path('C:/PALM/food_ai/ui/app.py')
        at = AppTest.from_file(str(app_path))
        at.run(timeout=30)

        self.assertEqual([item.value for item in at.title], ['PALM Food Vision Tester'])
        self.assertIn('Choose a local sample case', [item.label for item in at.selectbox])
        self.assertIn('Load Selected Sample', [item.label for item in at.button])

        at.button[0].click().run(timeout=30)
        self.assertTrue(str(at.session_state['uploaded_image_path']).lower().endswith(('.jpg', '.jpeg', '.png', '.webp')))
        self.assertFalse(at.error)

    def test_inference_service_accepts_sample_image_and_returns_pipeline_payload(self) -> None:
        sample_case = get_sample_image_cases(UISettings())[0]
        fake_result = PredictionResult(
            image_path=str(sample_case.path),
            predictions=[DishPrediction(label='waffles', name='waffles', confidence=0.82)],
            backend='pytorch',
            model_path='C:/PALM/artifacts/vision/classifier/food101_best.pt',
        )

        with patch('food_ai.ui.services.inference_service.get_runtime_diagnostics', return_value={'expected_env_match': True, 'warnings': [], 'degraded': False}), \
             patch('food_ai.ui.services.inference_service.predict_dish', return_value=fake_result):
            result = run_food_inference(sample_case.path, UIInferenceOptions())

        self.assertEqual(result['dish_fused_topk'][0]['name'], 'waffles')
        self.assertEqual(result['metadata']['inference_mode'], 'classifier_only')
        self.assertEqual(result['metadata']['backend'], 'pytorch')


if __name__ == '__main__':
    unittest.main()
