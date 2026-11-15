"""ODF/ODT reader preserving paragraph, character, table, list and image data.

The parser reads the ODF package XML directly. It never extracts external
resources from the network; only package-embedded images are loaded.
"""
from __future__ import annotations

import base64
import mimetypes
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .models import BookDocument, ImageAsset, TextBlock, TextRun, TextStyle

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


def q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _length(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(pt|px|in|cm|mm|pc)?\s*", value.lower())
    if not match:
        return None
    number, unit = match.groups()
    n = float(number)
    return n * {None: 1, "pt": 1, "px": .75, "in": 72, "cm": 28.3464567, "mm": 2.83464567, "pc": 12}[unit]


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes", "bold", "italic"}


def _text_content(element: ET.Element) -> str:
    return "".join(element.itertext())


class ODFLoader:
    SUPPORTED_EXTENSIONS = {".odt", ".fodt", ".ott", ".odm", ".odf"}

    def __init__(self) -> None:
        self.styles: dict[str, dict] = {}
        self.font_faces: dict[str, str] = {}
        self.images: list[ImageAsset] = []
        self._image_by_href: dict[str, ImageAsset] = {}
        self._package: zipfile.ZipFile | None = None

    def load(self, filename: str | Path) -> BookDocument:
        path = Path(filename).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported ODF extension: {ext}")

        self.styles, self.font_faces, self.images, self._image_by_href = {}, {}, [], {}
        metadata: dict = {}
        page_layout: dict = {}

        if ext == ".fodt":
            root = ET.parse(path).getroot()
            styles_root = root
        else:
            with zipfile.ZipFile(path) as package:
                names = set(package.namelist())
                if "content.xml" not in names:
                    raise ValueError("Invalid ODF package: content.xml is missing")
                root = ET.fromstring(package.read("content.xml"))
                styles_root = ET.fromstring(package.read("styles.xml")) if "styles.xml" in names else root
                metadata = self._metadata(package, names)
                self._extract_images(package)
                self._read_styles(styles_root)
                self._read_styles(root)
                page_layout = self._page_layout(styles_root)
                blocks = self._parse_document(root)
                return self._document(path, blocks, metadata, page_layout)

        self._read_styles(styles_root)
        self._read_styles(root)
        blocks = self._parse_document(root)
        return self._document(path, blocks, metadata, page_layout)

    def _document(self, path, blocks, metadata, page_layout):
        title = str(metadata.get("title") or path.stem.replace("_", " ").replace("-", " ").title())
        if not metadata.get("title"):
            for block in blocks:
                if block.kind in {"heading", "title"} and block.text.strip():
                    title = block.text.strip()
                    break
        return BookDocument(path, title, path.suffix.lower(), blocks,
                            list(self.images), metadata, dict(self.styles), page_layout)

    def _metadata(self, package, names):
        result = {}
        if "meta.xml" not in names:
            return result
        root = ET.fromstring(package.read("meta.xml"))
        mapping = {"title": "title", "subject": "subject", "creator": "creator",
                   "description": "description", "language": "language",
                   "date": "date", "creation-date": "creation-date",
                   "generator": "generator", "keyword": "keyword"}
        for key, tag in mapping.items():
            found = [e.text for e in root.iter(q("dc", tag)) if e.text]
            if not found:
                found = [e.text for e in root.iter(q("meta", tag)) if e.text]
            if found:
                result[key] = found if key == "keyword" else found[0]
        return result

    def _extract_images(self, package):
        for name in package.namelist():
            normalized = posixpath.normpath(name)
            if not normalized.startswith("Pictures/") or normalized.endswith("/"):
                continue
            try:
                data = package.read(name)
            except (KeyError, OSError):
                continue
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            asset = ImageAsset(Path(name).name, mime, data, href=name)
            self.images.append(asset)
            self._image_by_href[name] = asset

    def _read_styles(self, root):
        for face in root.iter(q("style", "font-face")):
            face_name = face.get(q("style", "name"))
            family = face.get(q("svg", "font-family")) or face.get(q("style", "font-family"))
            if face_name and family:
                self.font_faces[face_name] = family.strip("\"'")
        for list_style in root.iter(q("text", "list-style")):
            list_name = list_style.get(q("style", "name"))
            if list_name:
                self.styles[f"__list_{list_name}"] = {
                    "name": list_name, "family": "list",
                    "levels": [
                        {"kind": _local(level.tag), "attributes": dict(level.attrib),
                         "properties": { _local(child.tag): dict(child.attrib) for child in level }}
                        for level in list_style
                    ],
                }
        for element in root.iter():
            if element.tag not in {q("style", "style"), q("style", "default-style")}:
                continue
            name = element.get(q("style", "name"))
            family = element.get(q("style", "family"), "text")
            if not name and element.tag == q("style", "default-style"):
                name = f"__default_{family}"
            if not name:
                continue
            props = {}
            for child in element:
                props.update(child.attrib)
            self.styles[name] = {
                "name": name, "family": family,
                "parent": element.get(q("style", "parent-style-name")),
                "properties": props,
            }

    def _resolve(self, name: str | None, seen=None) -> dict:
        if not name or name not in self.styles:
            return {}
        seen = set() if seen is None else seen
        if name in seen:
            return {}
        seen.add(name)
        entry = self.styles[name]
        result = self._resolve(entry.get("parent"), seen)
        result.update(entry.get("properties", {}))
        return result

    def _style_name(self, element, attr="style-name"):
        return element.get(q("text", attr)) or element.get(q("draw", attr)) or element.get(q("table", attr))

    def _text_style(self, props: dict) -> TextStyle:
        weight = props.get(q("fo", "font-weight")) or props.get(q("style", "font-weight"))
        italic = props.get(q("fo", "font-style")) or props.get(q("style", "font-style"))
        underline = props.get(q("style", "text-underline-style"))
        strike = props.get(q("style", "text-line-through-style"))
        decoration = props.get(q("style", "text-underline-type"))
        size = props.get(q("fo", "font-size")) or props.get(q("style", "font-size"))
        font = props.get(q("style", "font-name")) or props.get(q("fo", "font-family"))
        if font:
            font = self.font_faces.get(font.strip("\"'"), font)
        color = props.get(q("fo", "color"))
        background = props.get(q("fo", "background-color")) or props.get(q("style", " background-color"))
        position = (props.get(q("style", "text-position")) or "").lower()
        return TextStyle(
            bold=(weight or "").lower() in {"bold", "700", "800", "900"},
            italic=(italic or "").lower() in {"italic", "oblique"},
            underline=bool(underline and underline.lower() != "none"),
            size=_length(size), font=font.strip("\"'") if font else None,
            color=color, background_color=background,
            strike=bool(strike and strike.lower() != "none"),
            overline=(props.get(q("style", "text-overline-style"), "none") != "none"),
            superscript="super" in position, subscript="sub" in position,
            small_caps=props.get(q("fo", "font-variant")) == "small-caps",
            language=props.get(q("fo", "language")), raw=dict(props),
        )

    def _paragraph_properties(self, name):
        props = self._resolve(name)
        return {
            "align": props.get(q("fo", "text-align")) or props.get(q("style", "text-align")),
            "margin_left": props.get(q("fo", "margin-left")),
            "margin_right": props.get(q("fo", "margin-right")),
            "text_indent": props.get(q("fo", "text-indent")),
            "margin_top": props.get(q("fo", "margin-top")),
            "margin_bottom": props.get(q("fo", "margin-bottom")),
            "line_height": props.get(q("fo", "line-height")),
            "background_color": props.get(q("fo", "background-color")),
            "break_before": props.get(q("fo", "break-before")),
            "break_after": props.get(q("fo", "break-after")),
            "keep_together": props.get(q("fo", "keep-together")),
            "raw": props,
        }

    def _page_layout(self, root):
        result = {"layouts": {}, "master_pages": []}
        for layout in root.iter(q("style", "page-layout")):
            name = layout.get(q("style", "name"))
            props = layout.find("style:page-layout-properties", NS)
            entry = dict(props.attrib) if props is not None else {}
            for child in layout:
                if child.tag != q("style", "page-layout-properties"):
                    entry[_local(child.tag)] = dict(child.attrib)
            result["layouts"][name or "default"] = entry
        for master in root.iter(q("style", "master-page")):
            entry = {"name": master.get(q("style", "name")),
                     "page_layout_name": master.get(q("style", "page-layout-name"))}
            for child in master:
                if child.tag in {q("style", "header"), q("style", "footer"),
                                 q("style", "header-left"), q("style", "footer-left")}:
                    entry[_local(child.tag)] = "\n".join(
                        _text_content(p) for p in child.iter()
                        if p.tag in {q("text", "p"), q("text", "h")}
                    )
            result["master_pages"].append(entry)
        return result

    def _parse_document(self, root):
        body = root.find(".//office:body", NS)
        if body is None:
            body = root
        text_root = body.find("office:text", NS)
        if text_root is None:
            text_root = body
        return self._parse_children(text_root)

    def _parse_children(self, parent):
        blocks = []
        for child in list(parent):
            tag = child.tag
            if tag == q("text", "p"):
                block = self._paragraph(child, "paragraph")
                if block.text or block.runs:
                    blocks.append(block)
            elif tag == q("text", "h"):
                block = self._paragraph(child, "heading")
                block.attributes["outline_level"] = child.get(q("text", "outline-level"), "1")
                if block.text or block.runs:
                    blocks.append(block)
            elif tag == q("text", "list"):
                blocks.append(self._list(child))
            elif tag == q("table", "table"):
                blocks.append(self._table(child))
            elif tag == q("text", "section"):
                blocks.append(TextBlock("section", children=self._parse_children(child),
                                        attributes={"name": child.get(q("text", "name"))}))
            elif tag in {q("text", "soft-page-break")}:
                blocks.append(TextBlock("page_break"))
            elif tag == q("text", "table-of-content"):
                blocks.append(TextBlock("toc", children=self._parse_children(child)))
            elif tag in {q("text", "tracked-changes"), q("office", "forms")}:
                continue
            elif tag in {q("text", "sequence-decls"), q("text", "user-field-decls")}:
                continue
            elif tag == q("text", "change-start"):
                continue
            elif tag == q("text", "illustration-index"):
                blocks.append(TextBlock("index", children=self._parse_children(child)))
            elif tag == q("text", "note"):
                blocks.append(TextBlock("note", runs=[TextRun(_text_content(child))]))
        return blocks

    def _paragraph(self, element, kind):
        name = element.get(q("text", "style-name"))
        props = self._paragraph_properties(name)
        attrs = {k: v for k, v in props.items() if k != "raw" and v is not None}
        attrs["outline_level"] = element.get(q("text", "outline-level"))
        attrs["class_names"] = element.get(q("text", "class-names"))
        attrs["raw_style"] = props.get("raw", {})
        base_text_style = self._text_style(self._resolve(name) or self._resolve("__default_paragraph"))
        return TextBlock(kind, self._inline_runs(element, base_text_style), props.get("align"), name,
                         attributes=attrs, style=base_text_style)

    def _inline_runs(self, parent, inherited=None, link=None):
        inherited = inherited or self._text_style(self._resolve("__default_text"))
        runs = []
        if parent.text:
            runs.append(TextRun(parent.text, inherited, link))
        for child in list(parent):
            tag = child.tag
            name = child.get(q("text", "style-name")) or child.get(q("draw", "style-name"))
            props = self._resolve(name)
            child_style = self._text_style(props) if props else inherited
            if props and inherited.raw:
                merged = dict(inherited.raw); merged.update(props)
                child_style = self._text_style(merged)
            child_link = link
            if tag == q("text", "a"):
                child_link = child.get(q("xlink", "href"))
            if tag == q("text", "s"):
                count = int(child.get(q("text", "c"), "1"))
                runs.append(TextRun(" " * max(1, count), child_style, child_link))
            elif tag == q("text", "tab"):
                runs.append(TextRun("\t", child_style, child_link))
            elif tag == q("text", "line-break"):
                runs.append(TextRun("\n", child_style, child_link))
            elif tag == q("draw", "frame"):
                runs.extend(self._frame_runs(child, child_style, child_link))
            elif tag in {q("text", "note"), q("text", "ruby")}:
                value = _text_content(child)
                if value:
                    runs.append(TextRun(value, child_style, child_link))
            else:
                runs.extend(self._inline_runs(child, child_style, child_link))
            if child.tail:
                runs.append(TextRun(child.tail, inherited, link))
        return runs

    def _frame_runs(self, frame, style, link):
        result = []
        for image_node in frame.iter(q("draw", "image")):
            href = image_node.get(q("xlink", "href"), "")
            asset = self._get_image(href)
            if asset:
                asset.width = frame.get(q("svg", "width"))
                asset.height = frame.get(q("svg", "height"))
                asset.alt_text = image_node.get(q("svg", "title")) or image_node.get(q("svg", "desc"))
                asset.title = image_node.get(q("svg", "title"))
                asset.properties = dict(frame.attrib)
                asset.properties.update({"wrap": frame.get(q("style", "wrap")),
                                         "run_through": frame.get(q("style", "run-through")),
                                         "anchor_type": frame.get(q("text", "anchor-type")),
                                         "z_index": frame.get(q("draw", "z-index"))})
                result.append(TextRun("", style, link, asset))
        return result

    def _get_image(self, href):
        if not href:
            return None
        href = unquote(href)
        if href.startswith("data:"):
            try:
                header, payload = href.split(",", 1)
                mime = header[5:].split(";", 1)[0] or "application/octet-stream"
                data = base64.b64decode(payload) if ";base64" in header else unquote(payload).encode()
                name = f"embedded-{len(self.images) + 1}"
                asset = ImageAsset(name, mime, data, href=href)
                self.images.append(asset)
                return asset
            except Exception:
                return None
        normalized = posixpath.normpath(href)
        return self._image_by_href.get(normalized) or self._image_by_href.get(posixpath.basename(normalized))

    def _list(self, element):
        items = []
        for item in element.findall("text:list-item", NS) + element.findall("text:ordered-list", NS):
            items.append(TextBlock("list_item", children=self._parse_children(item)))
        for item in element.findall("text:list-header", NS):
            items.append(TextBlock("list_header", children=self._parse_children(item)))
        list_style_name = element.get(q("text", "style-name"))
        return TextBlock("list", children=items,
                         attributes={"style_name": list_style_name,
                                     "list_style": self.styles.get(f"__list_{list_style_name}", {}),
                                     "continue_numbering": element.get(q("text", "continue-numbering")),
                                     "continue_list": element.get(q("text", "continue-list"))})

    def _table(self, element):
        rows = []
        for row in element.iter(q("table", "table-row")):
            cells = []
            for cell in list(row):
                if cell.tag not in {q("table", "table-cell"), q("table", "covered-table-cell")}:
                    continue
                cells.append(TextBlock("table_cell", children=self._parse_children(cell),
                    attributes={"column_span": int(cell.get(q("table", "number-columns-spanned"), "1")),
                                "row_span": int(cell.get(q("table", "number-rows-spanned"), "1")),
                                "covered": cell.tag == q("table", "covered-table-cell"),
                                "style_name": cell.get(q("table", "style-name"))}))
            repeat = max(1, int(row.get(q("table", "number-rows-repeated"), "1")))
            rows.extend([TextBlock("table_row", children=cells) for _ in range(min(repeat, 100))])
        return TextBlock("table", children=rows,
                         attributes={"name": element.get(q("table", "name")),
                                     "style_name": element.get(q("table", "style-name"))})


def load_odf(path: str | Path) -> BookDocument:
    return ODFLoader().load(path)
