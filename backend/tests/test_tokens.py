from __future__ import annotations

import pytest

from app.tokens import (
    VisionConfig,
    estimate_image_tokens,
    pixel_size_for_dpi,
    smart_resize,
)

QWEN36 = VisionConfig()  # patch 16, merge 2 -> 32 px per token edge


def test_default_config_matches_qwen36_preprocessor():
    assert QWEN36.patch_size == 16
    assert QWEN36.merge_size == 2
    assert QWEN36.factor == 32
    assert QWEN36.min_pixels == 65536
    assert QWEN36.max_pixels == 16777216


def test_resize_rounds_to_the_token_grid():
    width, height = smart_resize(1000, 700, QWEN36)
    assert width % 32 == 0 and height % 32 == 0


def test_token_count_is_the_grid_area_plus_wrappers():
    width, height = 1024, 1024
    tokens = estimate_image_tokens(width, height, QWEN36)
    assert tokens == (1024 // 32) * (1024 // 32) + 2  # 32*32 + 2


def test_oversized_images_are_clamped_to_max_pixels():
    width, height = smart_resize(20000, 20000, QWEN36)
    assert width * height <= QWEN36.max_pixels


def test_tiny_images_are_scaled_up_to_min_pixels():
    width, height = smart_resize(40, 40, QWEN36)
    assert width * height >= QWEN36.min_pixels


def test_aspect_ratio_is_preserved_when_clamping():
    width, height = smart_resize(8000, 4000, QWEN36)
    assert abs((width / height) - 2.0) < 0.05


def test_tokens_grow_roughly_with_the_square_of_the_dpi():
    """The DPI hint in the ingest error message relies on this relationship."""
    a4_pt = (595.0, 842.0)
    low = estimate_image_tokens(*pixel_size_for_dpi(*a4_pt, 100), QWEN36)
    high = estimate_image_tokens(*pixel_size_for_dpi(*a4_pt, 200), QWEN36)
    assert 3.5 < high / low < 4.5


def test_a4_at_150_dpi_is_a_plausible_page_cost():
    width, height = pixel_size_for_dpi(595.0, 842.0, 150)
    assert (width, height) == (1240, 1754)
    tokens = estimate_image_tokens(width, height, QWEN36)
    # 1240x1754 -> 1248x1760 on the 32px grid -> 39*55 = 2145 (+2 wrappers)
    assert tokens == 2147


def test_max_edge_caps_the_rendered_size():
    width, height = pixel_size_for_dpi(595.0, 842.0, 300, max_edge=2048)
    assert max(width, height) == 2048


def test_qwen25_style_config_still_works():
    """A 14/2 model must give a different, larger token count for the same page."""
    qwen25 = VisionConfig(patch_size=14, merge_size=2, min_pixels=3136, max_pixels=12845056)
    assert qwen25.factor == 28
    assert estimate_image_tokens(1240, 1754, qwen25) > estimate_image_tokens(
        1240, 1754, QWEN36
    )


@pytest.mark.parametrize("dpi", [72, 96, 150, 200, 300])
def test_estimation_is_deterministic(dpi: int):
    size = pixel_size_for_dpi(595.0, 842.0, dpi)
    first = estimate_image_tokens(*size, QWEN36)
    assert all(estimate_image_tokens(*size, QWEN36) == first for _ in range(10))
