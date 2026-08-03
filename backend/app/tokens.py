"""Vision token estimation for Qwen3-VL style encoders.

The spec assumed a 28x28 pixel grid. That is the Qwen2-VL / Qwen2.5-VL value
(``patch_size: 14``, ``merge_size: 2``). ``Qwen/Qwen3.6-27B`` ships

    {"patch_size": 16, "merge_size": 2,
     "size": {"shortest_edge": 65536, "longest_edge": 16777216}}

so one visual token covers ``16 * 2 = 32`` pixels per side. All four numbers are
settings rather than constants, so pointing the system at a different VLM is a
configuration change and not a code change.

``smart_resize`` reproduces the processor's own resizing rule: round both edges
to a multiple of ``factor``, then rescale (preserving aspect ratio) until the
total pixel count lies inside ``[min_pixels, max_pixels]``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Defaults read from Qwen/Qwen3.6-27B's preprocessor_config.json.
DEFAULT_PATCH_SIZE = 16
DEFAULT_MERGE_SIZE = 2
DEFAULT_MIN_PIXELS = 65536  # 256 x 256
DEFAULT_MAX_PIXELS = 16777216  # 4096 x 4096

#: <|vision_start|> and <|vision_end|> wrap every image in the Qwen chat format.
VISION_WRAPPER_TOKENS = 2


@dataclass(frozen=True)
class VisionConfig:
    patch_size: int = DEFAULT_PATCH_SIZE
    merge_size: int = DEFAULT_MERGE_SIZE
    min_pixels: int = DEFAULT_MIN_PIXELS
    max_pixels: int = DEFAULT_MAX_PIXELS

    @property
    def factor(self) -> int:
        """Pixels covered by one visual token along each edge."""
        return self.patch_size * self.merge_size


def _round_by_factor(value: int, factor: int) -> int:
    return max(factor, int(round(value / factor)) * factor)


def _floor_by_factor(value: float, factor: int) -> int:
    return max(factor, int(math.floor(value / factor)) * factor)


def _ceil_by_factor(value: float, factor: int) -> int:
    return max(factor, int(math.ceil(value / factor)) * factor)


def smart_resize(
    width: int, height: int, config: VisionConfig | None = None
) -> tuple[int, int]:
    """Return the (width, height) the processor will actually feed the encoder."""
    cfg = config or VisionConfig()
    f = cfg.factor
    if width <= 0 or height <= 0:
        return f, f

    w = _round_by_factor(width, f)
    h = _round_by_factor(height, f)

    if w * h > cfg.max_pixels:
        beta = math.sqrt((width * height) / cfg.max_pixels)
        w = _floor_by_factor(width / beta, f)
        h = _floor_by_factor(height / beta, f)
    elif w * h < cfg.min_pixels:
        beta = math.sqrt(cfg.min_pixels / (width * height))
        w = _ceil_by_factor(width * beta, f)
        h = _ceil_by_factor(height * beta, f)

    return w, h


def estimate_image_tokens(
    width: int, height: int, config: VisionConfig | None = None
) -> int:
    """Visual tokens for one image, including the two wrapper tokens."""
    cfg = config or VisionConfig()
    w, h = smart_resize(width, height, cfg)
    return (w // cfg.factor) * (h // cfg.factor) + VISION_WRAPPER_TOKENS


def pixel_size_for_dpi(
    pdf_width_pt: float, pdf_height_pt: float, dpi: int, max_edge: int | None = None
) -> tuple[int, int]:
    """PDF points (1/72 inch) at a given DPI, optionally capped by ``max_edge``."""
    scale = dpi / 72.0
    w = max(1, int(round(pdf_width_pt * scale)))
    h = max(1, int(round(pdf_height_pt * scale)))
    if max_edge and max(w, h) > max_edge:
        shrink = max_edge / max(w, h)
        w = max(1, int(round(w * shrink)))
        h = max(1, int(round(h * shrink)))
    return w, h


def estimate_text_tokens(text: str) -> int:
    """Rough character-based estimate for text that never dominates the budget.

    We do not ship a tokenizer: it would have to match the server's exactly to
    be worth anything, and a mismatch would give false precision. ~3.5 chars per
    token is a reasonable German/English mix, rounded up so estimates err on the
    conservative side of the context budget.
    """
    if not text:
        return 0
    return int(math.ceil(len(text) / 3.5))
