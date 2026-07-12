from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
THUMBNAIL_SIZE = (64, 64)
THUMBNAIL_BACKGROUND = (31, 36, 48, 255)
THUMBNAIL_FOREGROUND = (226, 232, 240, 255)


async def load_config() -> dict[str, Any]:
    """Load the application configuration without blocking Flet's event loop."""
    return await asyncio.to_thread(_load_config_sync)


def _load_config_sync() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    required_sections = {"paths", "ollama", "naming", "processing"}
    missing_sections = required_sections.difference(config)
    if missing_sections:
        missing = ", ".join(sorted(missing_sections))
        raise ValueError(f"config.json is missing required sections: {missing}")

    return config


async def get_thumbnail(file_path: str | Path) -> str:
    """
    Return a 64x64 PNG thumbnail as a plain base64 string.

    Pillow handles image files. PyMuPDF renders the first page of PDFs, then
    Pillow normalizes the result. All blocking work runs in a worker thread.
    """
    path = Path(file_path)
    return await asyncio.to_thread(_get_thumbnail_sync, path)


def _get_thumbnail_sync(file_path: Path) -> str:
    try:
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            source_image = _render_pdf_thumbnail_source(file_path)
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            source_image = _open_image_safely(file_path)
        else:
            return _create_file_type_thumbnail(file_path)

        with source_image:
            normalized = ImageOps.exif_transpose(source_image).convert("RGBA")
            normalized.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            canvas = Image.new("RGBA", THUMBNAIL_SIZE, THUMBNAIL_BACKGROUND)
            x = (THUMBNAIL_SIZE[0] - normalized.width) // 2
            y = (THUMBNAIL_SIZE[1] - normalized.height) // 2
            canvas.alpha_composite(normalized, (x, y))
            return _image_to_base64_png(canvas)

    except (
        FileNotFoundError,
        PermissionError,
        OSError,
        ValueError,
        RuntimeError,
        UnidentifiedImageError,
        fitz.FileDataError,
        fitz.EmptyFileError,
    ):
        return _create_file_type_thumbnail(file_path, error=True)


def _open_image_safely(file_path: Path) -> Image.Image:
    image = Image.open(file_path)
    image.load()
    return image


def _render_pdf_thumbnail_source(file_path: Path) -> Image.Image:
    with fitz.open(file_path) as document:
        if document.page_count < 1:
            raise ValueError("PDF contains no pages")
        if document.needs_pass:
            raise PermissionError("PDF is password protected")

        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=96, colorspace=fitz.csRGB, alpha=False)
        png_bytes = pixmap.tobytes("png")

    image = Image.open(io.BytesIO(png_bytes))
    image.load()
    return image


def _create_file_type_thumbnail(file_path: Path, error: bool = False) -> str:
    canvas = Image.new("RGBA", THUMBNAIL_SIZE, THUMBNAIL_BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    inset = 9
    draw.rounded_rectangle(
        (inset, 6, THUMBNAIL_SIZE[0] - inset, THUMBNAIL_SIZE[1] - 6),
        radius=7,
        fill=(46, 54, 72, 255),
        outline=(107, 119, 147, 255),
        width=1,
    )
    draw.line((43, 6, 55, 18), fill=(107, 119, 147, 255), width=1)
    draw.line((43, 6, 43, 18), fill=(107, 119, 147, 255), width=1)
    draw.line((43, 18, 55, 18), fill=(107, 119, 147, 255), width=1)

    label = "ERR" if error else file_path.suffix.upper().lstrip(".")[:4] or "FILE"
    font = ImageFont.load_default()
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    draw.text(
        ((THUMBNAIL_SIZE[0] - text_width) / 2, 35 - text_height / 2),
        label,
        fill=THUMBNAIL_FOREGROUND,
        font=font,
    )

    return _image_to_base64_png(canvas)


def _image_to_base64_png(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
