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
        """Load every package-embedded image and index it by its ODF href."""
        manifest_types = {}
        try:
            manifest = ET.fromstring(package.read("META-INF/manifest.xml"))
            for entry in manifest.iter(q("manifest", "file-entry")):
                path = entry.get(q("manifest", "full-path"))
                media = entry.get(q("manifest", "media-type"))
                if path and media:
                    manifest_types[posixpath.normpath(path)] = media
        except (KeyError, ET.ParseError, OSError):
            pass

        known_image_types = {
            ".svg": "image/svg+xml", ".svgz": "image/svg+xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".tif": "image/tiff", ".tiff": "image/tiff", ".ico": "image/x-icon",
        }
        for name in package.namelist():
            if not name or name.endswith("/"):
                continue
            normalized = posixpath.normpath(name).lstrip("/")
            if normalized in {"mimetype", "META-INF/manifest.xml"}:
                continue
            mime = (manifest_types.get(normalized)
                    or mimetypes.guess_type(normalized)[0]
                    or known_image_types.get(Path(normalized).suffix.lower()))
            if not mime or not mime.lower().startswith("image/"):
                continue
            try:
                data = package.read(name)
            except (KeyError, OSError, RuntimeError):
                continue
            asset = ImageAsset(Path(normalized).name, mime, data, href=normalized)
            self.images.append(asset)
            self._image_by_href[normalized] = asset
            self._image_by_href[name] = asset
            self._image_by_href.setdefault(posixpath.basename(normalized), asset)

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
                        {
                            "kind": _local(level.tag),
                            "bullet_char": level.get(q("text", "bullet-char")),
                            "num_format": level.get(q("style", "num-format")),
                            "num_prefix": level.get(q("style", "num-prefix"), ""),
                            "num_suffix": level.get(q("style", "num-suffix"), "."),
                            "start_value": level.get(q("text", "start-value"), "1"),
                            "attributes": dict(level.attrib),
                            "properties": {_local(child.tag): dict(child.attrib) for child in level},
                        }
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

    def _resolve_for_family(self, name: str | None, family: str) -> dict:
        """Resolve default text + family defaults + named/automatic style inheritance."""
        result = {}
        if family != "text":
            result.update(self._resolve("__default_text"))
        result.update(self._resolve(f"__default_{family}"))
        if name:
            entry = self.styles.get(name)
            if entry and entry.get("family") in {None, family, "text" if family == "text" else family}:
                result.update(self._resolve(name))
            elif entry:
                # Paragraph styles also provide character properties to their own runs.
                result.update(self._resolve(name))
        return result

    def _style_name(self, element, attr="style-name"):
        return element.get(q("text", attr)) or element.get(q("draw", attr)) or element.get(q("table", attr))

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
        props = self._resolve_for_family(name, "paragraph")
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
            "keep_with_next": props.get(q("fo", "keep-with-next")),
            "widows": props.get(q("fo", "widows")),
            "orphans": props.get(q("fo", "orphans")),
            "border": props.get(q("fo", "border")),
            "padding": props.get(q("fo", "padding")),
            "direction": props.get(q("fo", "direction")),
            "writing_mode": props.get(q("style", "writing-mode")),
            "text_transform": props.get(q("fo", "text-transform")),
            "hyphenate": props.get(q("fo", "hyphenate")),
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
            elif tag == q("draw", "frame"):
                style_name = child.get(q("draw", "style-name"))
                frame_style = self._text_style(self._resolve_for_family(style_name, "graphic"))
                runs = self._frame_runs(child, frame_style, None)
                if runs:
                    blocks.append(TextBlock("paragraph", runs=runs, style=frame_style,
                                            attributes={"anchored_frame": True,
                                                        "anchor_type": child.get(q("text", "anchor-type")),
                                                        "frame_style": style_name}))
            elif tag == q("draw", "g"):
                blocks.extend(self._parse_children(child))
            elif tag in {q("draw", "custom-shape"), q("draw", "rect"),
                         q("draw", "ellipse"), q("draw", "line"),
                         q("draw", "connector"), q("draw", "polygon"),
                         q("draw", "polyline"), q("draw", "path")}:
                value = _text_content(child).strip()
                if value:
                    blocks.append(TextBlock("paragraph", runs=[TextRun(value)],
                                            attributes={"drawing_shape": _local(tag)}))
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
        attrs["style_properties"] = props.get("raw", {})
        base_text_style = self._text_style(props.get("raw", {}))
        return TextBlock(kind, self._inline_runs(element, base_text_style), props.get("align"), name,
                         attributes=attrs, style=base_text_style)

    def _inline_runs(self, parent, inherited=None, link=None):
        inherited = inherited or self._text_style(self._resolve_for_family(None, "text"))
        runs = []
        if parent.text:
            runs.append(TextRun(parent.text, inherited, link))
        for child in list(parent):
            tag = child.tag
            name = child.get(q("text", "style-name"))
            if tag == q("draw", "frame"):
                runs.extend(self._frame_runs(child, inherited, link))
            elif tag in {q("text", "s"), q("text", "tab"), q("text", "line-break")}:
                if name:
                    merged = dict(inherited.raw)
                    merged.update(self._resolve_for_family(name, "text"))
                    child_style = self._text_style(merged)
                else:
                    child_style = inherited
                child_link = link
                if tag == q("text", "s"):
                    try:
                        count = max(1, min(10000, int(child.get(q("text", "c"), "1"))))
                    except (TypeError, ValueError):
                        count = 1
                    runs.append(TextRun(" " * count, child_style, child_link))
                elif tag == q("text", "tab"):
                    runs.append(TextRun("\t", child_style, child_link))
                else:
                    runs.append(TextRun("\n", child_style, child_link))
            elif tag == q("text", "a"):
                child_link = child.get(q("xlink", "href")) or link
                child_props = self._resolve_for_family(name, "text") if name else {}
                merged = dict(inherited.raw)
                merged.update(child_props)
                merged.update({key: value for key, value in child.attrib.items()
                               if key.startswith("{" + NS["fo"] + "}")})
                child_style = self._text_style(merged) if child_props or merged else inherited
                runs.extend(self._inline_runs(child, child_style, child_link))
            elif tag in {q("text", "note"), q("text", "ruby")}:
                value = _text_content(child)
                if value:
                    runs.append(TextRun(value, inherited, link))
            elif tag in {q("draw", "custom-shape"), q("draw", "rect"),
                         q("draw", "ellipse"), q("draw", "line"),
                         q("draw", "connector"), q("draw", "polygon"),
                         q("draw", "polyline"), q("draw", "path")}:
                value = _text_content(child).strip()
                if value:
                    runs.append(TextRun(value, inherited, link))
            else:
                child_props = self._resolve_for_family(name, "text") if name else {}
                merged = dict(inherited.raw)
                merged.update(child_props)
                merged.update({key: value for key, value in child.attrib.items()
                               if key.startswith("{" + NS["fo"] + "}")})
                child_style = self._text_style(merged) if child_props or merged else inherited
                child_link = link
                runs.extend(self._inline_runs(child, child_style, child_link))
            if child.tail:
                runs.append(TextRun(child.tail, inherited, link))
        return runs

    def _frame_runs(self, frame, style, link):
        result = []
        image_nodes = list(frame.iter(q("draw", "image")))
        for image_node in image_nodes:
            href = image_node.get(q("xlink", "href"), "")
            asset = self._get_image(href)
            if asset:
                frame_asset = ImageAsset(
                    asset.name, asset.mime_type, asset.data, href=asset.href,
                    width=frame.get(q("svg", "width")),
                    height=frame.get(q("svg", "height")),
                    alt_text=(image_node.get(q("svg", "title"))
                              or image_node.get(q("svg", "desc"))
                              or frame.get(q("svg", "title"))
                              or frame.get(q("svg", "desc"))),
                    title=image_node.get(q("svg", "title")) or frame.get(q("svg", "title")),
                    properties=dict(frame.attrib),
                )
                frame_asset.properties.update({
                    "wrap": frame.get(q("style", "wrap")),
                    "run_through": frame.get(q("style", "run-through")),
                    "anchor_type": frame.get(q("text", "anchor-type")),
                    "z_index": frame.get(q("draw", "z-index")),
                    "horizontal_pos": frame.get(q("style", "horizontal-pos")),
                    "horizontal_rel": frame.get(q("style", "horizontal-rel")),
                    "vertical_pos": frame.get(q("style", "vertical-pos")),
                    "vertical_rel": frame.get(q("style", "vertical-rel")),
                })
                result.append(TextRun("", style, link, frame_asset))
            elif href:
                label = frame.get(q("svg", "title")) or posixpath.basename(href)
                result.append(TextRun(f"[Linked image: {label}]", style, link))

        for text_box in frame.iter(q("draw", "text-box")):
            paragraphs = [
                _text_content(node).strip()
                for node in text_box.iter()
                if node.tag in {q("text", "p"), q("text", "h")}
            ]
            text = "\n".join(value for value in paragraphs if value)
            if text:
                result.append(TextRun(text, style, link))

        if not result:
            embedded = next(iter(frame.iter(q("draw", "object"))), None)
            plugin = next(iter(frame.iter(q("draw", "plugin"))), None)
            object_node = embedded or plugin
            href = object_node.get(q("xlink", "href")) if object_node is not None else None
            if href:
                result.append(TextRun(
                    f"[Embedded object: {posixpath.basename(href.rstrip('/'))}]",
                    style, link,
                ))
        return result

    def _get_image(self, href):
        if not href:
            return None
        href = unquote(href).strip()
        if href.startswith("data:"):
            try:
                header, payload = href.split(",", 1)
                mime = header[5:].split(";", 1)[0] or "application/octet-stream"
                if ";base64" in header:
                    data = base64.b64decode(payload, validate=False)
                else:
                    from urllib.parse import unquote_to_bytes
                    data = unquote_to_bytes(payload)
                asset = ImageAsset(f"embedded-{len(self.images) + 1}", mime, data, href=href)
                self.images.append(asset)
                return asset
            except Exception:
                return None
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("//"):
            # Do not fetch remote images while opening a document.
            return None
        href = href.split("#", 1)[0].split("?", 1)[0]
        normalized = posixpath.normpath(href.lstrip("/"))
        candidates = [normalized]
        while normalized.startswith("../"):
            normalized = normalized[3:]
            candidates.append(normalized)
        candidates.extend([posixpath.basename(normalized),
                           "Pictures/" + posixpath.basename(normalized)])
        for candidate in candidates:
            asset = self._image_by_href.get(candidate)
            if asset:
                return asset
        basename = posixpath.basename(normalized)
        return next((asset for asset in self.images if asset.name == basename), None)

    def _list(self, element):
        items = []
        for item in list(element):
            if item.tag == q("text", "list-item"):
                items.append(TextBlock("list_item", children=self._parse_children(item)))
            elif item.tag == q("text", "list-header"):
                items.append(TextBlock("list_header", children=self._parse_children(item)))
        list_style_name = element.get(q("text", "style-name"))
        return TextBlock("list", children=items,
                         attributes={"style_name": list_style_name,
                                     "list_style": self.styles.get(f"__list_{list_style_name}", {}),
                                     "continue_numbering": element.get(q("text", "continue-numbering")),
                                     "continue_list": element.get(q("text", "continue-list")),
                                     "level": element.get(q("text", "level"), "1")})

    def _table(self, element):
        def safe_int(value, default=1):
            try:
                return max(1, min(1000, int(value)))
            except (TypeError, ValueError):
                return default

        table_style_name = element.get(q("table", "style-name"))
        table_props = self._resolve_for_family(table_style_name, "table")
        columns = []
        for child in list(element):
            if child.tag != q("table", "table-column"):
                continue
            style_name = child.get(q("table", "style-name"))
            column_props = self._resolve_for_family(style_name, "table-column")
            columns.append({
                "style_name": style_name,
                "width": column_props.get(q("style", "column-width")),
                "repeat": safe_int(child.get(q("table", "number-columns-repeated"), "1")),
                "properties": column_props,
            })

        row_nodes = []
        for child in list(element):
            if child.tag == q("table", "table-row"):
                row_nodes.append(child)
            elif child.tag in {q("table", "table-rows"), q("table", "table-header-rows")}:
                row_nodes.extend(row for row in list(child) if row.tag == q("table", "table-row"))

        rows = []
        for row in row_nodes:
            row_style_name = row.get(q("table", "style-name"))
            row_props = self._resolve_for_family(row_style_name, "table-row")
            cells = []
            for cell in list(row):
                if cell.tag not in {q("table", "table-cell"), q("table", "covered-table-cell")}:
                    continue
                cell_style_name = cell.get(q("table", "style-name"))
                cell_props = self._resolve_for_family(cell_style_name, "table-cell")
                cell_style = self._text_style(cell_props)
                cell_block = TextBlock(
                    "table_cell",
                    children=self._parse_children(cell),
                    style_name=cell_style_name,
                    style=cell_style,
                    attributes={
                        "column_span": safe_int(cell.get(q("table", "number-columns-spanned"), "1")),
                        "row_span": safe_int(cell.get(q("table", "number-rows-spanned"), "1")),
                        "covered": cell.tag == q("table", "covered-table-cell"),
                        "style_name": cell_style_name,
                        "style_properties": cell_props,
                        "background_color": cell_props.get(q("fo", "background-color")),
                        "vertical_align": cell_props.get(q("style", "vertical-align")),
                        "padding": cell_props.get(q("fo", "padding")),
                        "border": cell_props.get(q("fo", "border")),
                        "text_align": cell_props.get(q("fo", "text-align")),
                    },
                )
                repeat = safe_int(cell.get(q("table", "number-columns-repeated"), "1"))
                cells.extend([cell_block] * min(repeat, 100))
            repeat = safe_int(row.get(q("table", "number-rows-repeated"), "1"))
            for _ in range(min(repeat, 100)):
                rows.append(TextBlock("table_row", children=cells, style_name=row_style_name,
                                       attributes={"style_properties": row_props}))
        return TextBlock(
            "table",
            children=rows,
            style_name=table_style_name,
            attributes={
                "name": element.get(q("table", "name")),
                "style_name": table_style_name,
                "style_properties": table_props,
                "columns": columns,
            },
        )



def load_odf(path: str | Path) -> BookDocument:
    return ODFLoader().load(path)
