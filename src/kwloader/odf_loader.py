from pathlib import Path

from odf import teletype
from odf.element import Element
from odf.opendocument import load
from odf.style import Style
from odf.text import H, LineBreak, P, S, Span, Tab

from .models import BookDocument, TextBlock, TextRun, TextStyle


def _element_text(element) -> str:
    try:
        return teletype.extractText(element)
    except Exception:
        return ""


def _style_name(element) -> str:
    try:
        return (
            element.getAttribute("stylename")
            or element.getAttribute("style-name")
            or ""
        )
    except Exception:
        return ""


def _style_properties(document, style_name: str) -> TextStyle:
    result = TextStyle()

    if not style_name:
        return result

    collections = []
    for name in ("styles", "automaticstyles"):
        collection = getattr(document, name, None)
        if collection is not None:
            collections.append(collection)

    try:
        for collection in collections:
            for style in collection.getElementsByType(Style):
                name = (
                    style.getAttribute("name")
                    or style.getAttribute("stylename")
                    or ""
                )
                if name != style_name:
                    continue

                text_properties = None
                paragraph_properties = None

                for child in getattr(style, "childNodes", []):
                    qname = getattr(child, "qname", None)
                    if not qname or len(qname) < 2:
                        continue

                    child_name = qname[1]
                    if child_name == "text-properties":
                        text_properties = child
                    elif child_name == "paragraph-properties":
                        paragraph_properties = child

                if text_properties is not None:
                    weight = (
                        text_properties.getAttribute("fontweight")
                        or text_properties.getAttribute("font-weight")
                    )
                    italic = (
                        text_properties.getAttribute("fontstyle")
                        or text_properties.getAttribute("font-style")
                    )
                    underline = (
                        text_properties.getAttribute("textunderlinestyle")
                        or text_properties.getAttribute("text-underline-style")
                    )
                    size = (
                        text_properties.getAttribute("fontsize")
                        or text_properties.getAttribute("font-size")
                    )
                    font = (
                        text_properties.getAttribute("fontfamily")
                        or text_properties.getAttribute("font-family")
                    )

                    if weight:
                        result.bold = str(weight).lower() in {
                            "bold", "700", "800", "900"
                        }

                    if italic:
                        result.italic = str(italic).lower() in {
                            "italic", "oblique"
                        }

                    if underline:
                        result.underline = str(underline).lower() not in {
                            "", "none"
                        }

                    if size:
                        result.size = _size_to_points(size)

                    if font:
                        result.font = str(font)

                return result
    except Exception:
        pass

    return result


def _size_to_points(value) -> float | None:
    if not value:
        return None

    try:
        text = str(value).strip().lower()
        if text.endswith("pt"):
            return float(text[:-2])
        if text.endswith("px"):
            return float(text[:-2]) * 0.75
        if text.endswith("cm"):
            return float(text[:-2]) * 28.346
        if text.endswith("mm"):
            return float(text[:-2]) * 2.8346
    except Exception:
        return None

    return None


def _merge_style(base: TextStyle, override: TextStyle) -> TextStyle:
    # ODF style objects do not distinguish "unset" booleans, so only use
    # this for the values that are actually present in the source style.
    return TextStyle(
        bold=override.bold or base.bold,
        italic=override.italic or base.italic,
        underline=override.underline or base.underline,
        size=override.size if override.size is not None else base.size,
        font=override.font if override.font is not None else base.font,
    )


def _is(node, *factories) -> bool:
    qname = getattr(node, "qname", None)
    if qname is None:
        return False

    for factory in factories:
        try:
            if factory(check_grammar=False).qname == qname:
                return True
        except Exception:
            continue

    return False


def _collect_runs(node, document, inherited: TextStyle | None = None) -> list[TextRun]:
    inherited = inherited or TextStyle()
    style = inherited
    style_name = _style_name(node)

    if style_name:
        style = _merge_style(
            inherited,
            _style_properties(document, style_name),
        )

    if isinstance(node, str):
        return [TextRun(node, style)] if node else []

    if _is(node, S):
        try:
            count = int(node.getAttribute("c") or 1)
        except Exception:
            count = 1
        return [TextRun(" " * max(1, count), style)]

    if _is(node, Tab):
        return [TextRun("\t", style)]

    if _is(node, LineBreak):
        return [TextRun("\n", style)]

    if not isinstance(node, Element):
        return []

    runs: list[TextRun] = []
    for child in getattr(node, "childNodes", []):
        if isinstance(child, Element):
            runs.extend(_collect_runs(child, document, style))
        else:
            value = str(child)
            if value:
                runs.append(TextRun(value, style))

    return runs


def _paragraph_block(document, node) -> TextBlock:
    style_name = _style_name(node)
    style = _style_properties(document, style_name)

    align = None
    try:
        for collection_name in ("styles", "automaticstyles"):
            collection = getattr(document, collection_name, None)
            if collection is None:
                continue

            for odf_style in collection.getElementsByType(Style):
                name = (
                    odf_style.getAttribute("name")
                    or odf_style.getAttribute("stylename")
                    or ""
                )
                if name != style_name:
                    continue

                for child in getattr(odf_style, "childNodes", []):
                    qname = getattr(child, "qname", None)
                    if qname and len(qname) >= 2 and qname[1] == "paragraph-properties":
                        align = (
                            child.getAttribute("textalign")
                            or child.getAttribute("text-align")
                            or None
                        )
                        break
    except Exception:
        pass

    kind = "heading" if _is(node, H) else "paragraph"
    runs = _collect_runs(node, document, style)

    return TextBlock(
        kind=kind,
        runs=runs,
        align=str(align).lower() if align else None,
        style_name=style_name or None,
    )


def load_odf(path: str | Path) -> BookDocument:
    path = Path(path).resolve()
    document = load(str(path))

    blocks: list[TextBlock] = []
    stack = [document.text]

    while stack:
        node = stack.pop()
        if node is None or not isinstance(node, Element):
            continue

        if _is(node, P, H):
            if _element_text(node).strip():
                blocks.append(_paragraph_block(document, node))
            continue

        for child in reversed(getattr(node, "childNodes", [])):
            if isinstance(child, Element):
                stack.append(child)

    title = path.stem.replace("_", " ").replace("-", " ").title()

    for block in blocks:
        if block.kind == "heading" and block.text.strip():
            title = block.text.strip()
            break
        if block.kind == "title" and block.text.strip():
            title = block.text.strip
            break

    if title == path.stem.replace("_", " ").replace("-", " ").title():
        for block in blocks:
            if block.text.strip():
                title = block.text.strip()
                break

    return BookDocument(
        path=path,
        title=title,
        format=path.suffix.lower(),
        blocks=blocks,
    )
