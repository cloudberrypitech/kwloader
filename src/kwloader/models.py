"""Format-neutral document models shared by kwloader and kwiddle."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TextStyle:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    size: float | None = None
    font: str | None = None
    color: str | None = None
    background_color: str | None = None
    strike: bool = False
    overline: bool = False
    superscript: bool = False
    subscript: bool = False
    small_caps: bool = False
    letter_spacing: str | None = None
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageAsset:
    name: str
    mime_type: str
    data: bytes
    href: str | None = None
    width: str | None = None
    height: str | None = None
    alt_text: str | None = None
    title: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TextRun:
    text: str
    style: TextStyle = field(default_factory=TextStyle)
    link: str | None = None
    image: ImageAsset | None = None


@dataclass(slots=True)
class TextBlock:
    kind: str
    runs: list[TextRun] = field(default_factory=list)
    align: str | None = None
    style_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list["TextBlock"] = field(default_factory=list)
    image: ImageAsset | None = None
    style: TextStyle = field(default_factory=TextStyle)

    @property
    def text(self) -> str:
        own = "".join(run.text for run in self.runs)
        if self.kind == "image" and self.image and not own:
            own = self.image.alt_text or self.image.title or ""
        nested = "".join(child.text for child in self.children)
        if self.kind == "table":
            rows = []
            for row in self.children:
                rows.append("\t | \t".join(cell.text for cell in row.children))
            return "\n".join(rows)
        return own + nested


@dataclass(slots=True)
class BookDocument:
    path: Path
    title: str
    format: str
    blocks: list[TextBlock] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    styles: dict[str, Any] = field(default_factory=dict)
    page_layout: dict[str, Any] = field(default_factory=dict)

    def get_image(self, name: str) -> ImageAsset | None:
        return next((image for image in self.images if image.name == name), None)
