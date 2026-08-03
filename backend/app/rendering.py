"""Document → image conversion.

PDFs go through PyMuPDF's ``page.get_pixmap(dpi=…)``; standalone images are
normalised through the same downscale/format/grayscale path so that both kinds
of source produce pixels the model sees identically.

The base64 written here is the artefact that matters. It is produced exactly
once and read back verbatim on every subsequent request.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from .tokens import VisionConfig, estimate_image_tokens

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


@dataclass(frozen=True)
class RenderOptions:
    dpi: int = 150
    max_edge: int = 2048
    image_format: str = "png"  # "png" | "jpeg"
    jpeg_quality: int = 90
    grayscale: bool = False

    @property
    def mime_type(self) -> str:
        return "image/jpeg" if self.image_format == "jpeg" else "image/png"

    @property
    def suffix(self) -> str:
        return ".jpg" if self.image_format == "jpeg" else ".png"


@dataclass
class RenderedPage:
    page_number: int
    image_bytes: bytes
    base64_data: str
    width: int
    height: int
    byte_size: int
    image_hash: str
    estimated_tokens: int
    mime_type: str


def page_count(path: Path) -> int:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return 1
    with fitz.open(path) as doc:
        return doc.page_count


def _encode(image: Image.Image, options: RenderOptions) -> bytes:
    buffer = io.BytesIO()
    if options.image_format == "jpeg":
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=options.jpeg_quality, optimize=True)
    else:
        # optimize=True keeps PNG output deterministic for identical input and
        # avoids zlib level drift between Pillow builds changing the bytes.
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _postprocess(image: Image.Image, options: RenderOptions) -> Image.Image:
    if options.grayscale and image.mode != "L":
        image = image.convert("L")
    elif not options.grayscale and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    if options.max_edge and max(image.size) > options.max_edge:
        shrink = options.max_edge / max(image.size)
        new_size = (
            max(1, int(round(image.width * shrink))),
            max(1, int(round(image.height * shrink))),
        )
        image = image.resize(new_size, Image.LANCZOS)
    return image


def _finalize(
    image: Image.Image, page_number: int, options: RenderOptions, vision: VisionConfig
) -> RenderedPage:
    image = _postprocess(image, options)
    data = _encode(image, options)
    b64 = base64.b64encode(data).decode("ascii")
    return RenderedPage(
        page_number=page_number,
        image_bytes=data,
        base64_data=b64,
        width=image.width,
        height=image.height,
        byte_size=len(data),
        image_hash=hashlib.sha256(data).hexdigest(),
        estimated_tokens=estimate_image_tokens(image.width, image.height, vision),
        mime_type=options.mime_type,
    )


def render_document(
    path: Path, options: RenderOptions, vision: VisionConfig | None = None
):
    """Yield one :class:`RenderedPage` per page, in document order."""
    vision = vision or VisionConfig()

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as img:
            yield _finalize(img.copy(), 1, options, vision)
        return

    with fitz.open(path) as doc:
        for index in range(doc.page_count):
            pixmap = doc.load_page(index).get_pixmap(dpi=options.dpi, alpha=False)
            image = Image.frombytes(
                "RGB" if pixmap.n >= 3 else "L", (pixmap.width, pixmap.height), pixmap.samples
            )
            yield _finalize(image, index + 1, options, vision)


def probe_page_geometry(path: Path, options: RenderOptions) -> list[tuple[int, int]]:
    """Resulting pixel dimensions per page without doing the actual rendering.

    Used by the upload dialog's DPI slider so the live token estimate does not
    require rasterising a 300-page PDF on every drag.
    """
    from .tokens import pixel_size_for_dpi

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as img:
            width, height = img.size
        if options.max_edge and max(width, height) > options.max_edge:
            shrink = options.max_edge / max(width, height)
            width = max(1, int(round(width * shrink)))
            height = max(1, int(round(height * shrink)))
        return [(width, height)]

    sizes: list[tuple[int, int]] = []
    with fitz.open(path) as doc:
        for index in range(doc.page_count):
            rect = doc.load_page(index).rect
            sizes.append(
                pixel_size_for_dpi(rect.width, rect.height, options.dpi, options.max_edge)
            )
    return sizes


def write_page_files(
    page: RenderedPage, image_path: Path, base64_path: Path
) -> None:
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(page.image_bytes)
    # ASCII, no trailing newline: read back verbatim by prefix.read_base64.
    base64_path.write_text(page.base64_data, encoding="ascii")
