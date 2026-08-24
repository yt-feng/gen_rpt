# test_vlm_assessor.py
import pytest
from tools.regenerate_image import _assess_image_quality

def test_vlm_assessor_quality_score():
    # Test blank or mock bytes
    res_clean = _assess_image_quality(b"clean_image_mock_data_12345", "A professional photo")
    assert "passed" in res_clean
    assert "score" in res_clean
    assert "artifacts" in res_clean
