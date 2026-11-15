"""DOCX/DOTX loader retaining formatted text, tables and embedded images."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import BookDocument, ImageAsset, TextBlock, TextRun, TextStyle

_WORD_DOCUMENT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_WORD_TEMPLATE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"


def _points(value):
    try:
        return float(value.pt) if value is not None else None
    except Exception:
        return None


def _style_font(paragraph):
    try:
        font = paragraph.style.font
        return TextStyle(bold=bool(font.bold), italic=bool(font.italic),
                         underline=bool(font.underline), size=_points(font.size),
                         font=font.name)
    except Exception:
        return TextStyle()


def _load_dotx(path: Path):
    buffer = BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(_WORD_TEMPLATE_CONTENT_TYPE.encode(), _WORD_DOCUMENT_CONTENT_TYPE.encode())
            target.writestr(item, data)
    buffer.seek(0)
    return Document(buffer)


def _load_document(path):
    return _load_dotx(path) if path.suffix.lower() == ".dotx" else Document(str(path))


def _paragraph_alignment(paragraph):
    return {WD_ALIGN_PARAGRAPH.CENTER: "center", WD_ALIGN_PARAGRAPH.RIGHT: "right",
            WD_ALIGN_PARAGRAPH.JUSTIFY: "justify", WD_ALIGN_PARAGRAPH.LEFT: "left"}.get(paragraph.alignment)


def _paragraph_kind(paragraph):
    try:
        name = (paragraph.style.name or "").lower()
    except Exception:
        name = ""
    return "heading" if name.startswith("heading") or name in {"title", "subtitle"} else "paragraph"


def _rgb(color):
    try:
        return f"#{color.rgb}" if color and color.rgb else None
    except Exception:
        return None


def load_word(path: str | Path) -> BookDocument:
    path = Path(path).resolve()
    document = _load_document(path)
    images = []
    image_by_part = {}

    # python-docx exposes image parts via relationships; retain the original bytes.
    for rel in document.part.rels.values():
        if "image" in rel.reltype:
            part = rel.target_part
            asset = ImageAsset(Path(str(part.partname)).name, part.content_type,
                               part.blob, href=str(part.partname))
            images.append(asset)
            image_by_part[rel.rId] = asset

    blocks = []
    for paragraph in document.paragraphs:
        base = _style_font(paragraph)
        runs = []
        for run in paragraph.runs:
            font = run.font
            style = TextStyle(
                bold=bool(run.bold) if run.bold is not None else base.bold,
                italic=bool(run.italic) if run.italic is not None else base.italic,
                underline=bool(run.underline) if run.underline is not None else base.underline,
                size=_points(font.size) if font.size is not None else base.size,
                font=font.name or base.font,
                color=_rgb(font.color),
                background_color=None,
                strike=bool(font.strike),
                superscript=bool(font.superscript),
                subscript=bool(font.subscript),
            )
            # Preserve drawing objects at their position within a paragraph.
            for node in run._element.iter():
                if node.tag.endswith("}blip"):
                    rid = node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                    asset = image_by_part.get(rid)
                    if asset:
                        runs.append(TextRun("", style, image=asset))
            if run.text:
                runs.append(TextRun(run.text, style))

        if not runs and paragraph.text:
            runs.append(TextRun(paragraph.text, base))
        if runs:
            blocks.append(TextBlock(_paragraph_kind(paragraph), runs,
                _paragraph_alignment(paragraph), getattr(paragraph.style, "name", None)))

    # Include document tables. Their content is represented as nested cells/rows.
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [TextBlock("table_cell", children=[
                TextBlock("paragraph", [TextRun(p.text)]) for p in cell.paragraphs if p.text
            ]) for cell in row.cells]
            rows.append(TextBlock("table_row", children=cells))
        blocks.append(TextBlock("table", children=rows))

    title = path.stem.replace("_", " ").replace("-", " ").title()
    for block in blocks:
        if block.kind == "heading" and block.text.strip():
            title = block.text.strip(); break
    metadata = {}
    try:
        props = document.core_properties
        for key in ("title", "subject", "author", "keywords", "comments", "created", "modified"):
            value = getattr(props, key, None)
            if value:
                metadata[key] = str(value)
        if props.title:
            title = props.title
    except Exception:
        pass
    return BookDocument(path, title, path.suffix.lower(), blocks, images, metadata)
