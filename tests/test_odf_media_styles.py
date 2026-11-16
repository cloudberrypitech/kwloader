from __future__ import annotations

import base64
import zipfile
from pathlib import Path

from kwloader import load_book


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jvWQAAAAASUVORK5CYII="
)

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink">
 <office:automatic-styles>
  <style:style style:name="ParentParagraph" style:family="paragraph">
   <style:paragraph-properties fo:text-align="center"/>
   <style:text-properties fo:font-weight="bold" fo:color="#aa2200"/>
  </style:style>
  <style:style style:name="ChildParagraph" style:family="paragraph" style:parent-style-name="ParentParagraph">
   <style:paragraph-properties fo:margin-left="1cm"/>
  </style:style>
  <style:style style:name="BlueCell" style:family="table-cell">
   <style:table-cell-properties fo:background-color="#abcdef" fo:border="0.5pt solid #000000"/>
  </style:style>
 </office:automatic-styles>
 <office:body><office:text>
  <text:p text:style-name="ChildParagraph">Before<text:s text:c="3"/>
   <draw:frame draw:name="Inline picture" text:anchor-type="as-char" svg:width="1in" svg:height="1in">
    <draw:image xlink:href="./Media/pixel.png" svg:title="One pixel"/>
   </draw:frame>After
  </text:p>
  <draw:frame draw:name="Page anchored picture" text:anchor-type="page" svg:width="1in" svg:height="1in">
   <draw:image xlink:href="Media/pixel.png" svg:title="Page image"/>
  </draw:frame>
  <draw:g>
   <draw:frame draw:name="Grouped picture" text:anchor-type="paragraph" svg:width="1in" svg:height="1in">
    <draw:image xlink:href="Media/pixel.png" svg:title="Grouped image"/>
   </draw:frame>
  </draw:g>
  <draw:frame draw:name="Text box" text:anchor-type="paragraph">
   <draw:text-box><text:p>Text inside a drawing frame</text:p></draw:text-box>
  </draw:frame>
  <draw:frame draw:name="Embedded chart preview" svg:title="Sales chart">
   <draw:object xlink:href="./Chart 1"/>
  </draw:frame>
  <table:table table:name="Styled table">
   <table:table-row>
    <table:table-cell table:style-name="BlueCell"><text:p>Cell value</text:p></table:table-cell>
   </table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">
 <office:styles>
  <style:default-style style:family="paragraph">
   <style:text-properties fo:font-size="12pt"/>
  </style:default-style>
 </office:styles>
</office:document-styles>
"""

MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest
 xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="Media/pixel.png" manifest:media-type="image/png"/>
 <manifest:file-entry manifest:full-path="ObjectReplacements/Chart 1" manifest:media-type="image/png"/>
</manifest:manifest>
"""


def make_odt(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        package.writestr("content.xml", CONTENT_XML)
        package.writestr("styles.xml", STYLES_XML)
        package.writestr("META-INF/manifest.xml", MANIFEST_XML)
        package.writestr("Media/pixel.png", PIXEL_PNG)
        package.writestr("ObjectReplacements/Chart 1", PIXEL_PNG)


def test_inline_package_image_and_inherited_paragraph_style(tmp_path: Path) -> None:
    odt = tmp_path / "image-and-styles.odt"
    make_odt(odt)

    document = load_book(odt)
    paragraph = document.blocks[0]

    assert paragraph.align == "center"
    assert paragraph.attributes["margin_left"] == "1cm"
    assert paragraph.style.bold is True
    assert paragraph.style.color == "#aa2200"

    image_runs = [run for run in paragraph.runs if run.image is not None]
    assert len(image_runs) == 1
    assert image_runs[0].image is not None
    assert image_runs[0].image.mime_type == "image/png"
    assert image_runs[0].image.width == "1in"
    assert image_runs[0].image.alt_text == "One pixel"
    assert image_runs[0].image.data == PIXEL_PNG

    assert any(run.text == "   " for run in paragraph.runs)

    page_frame = document.blocks[1]
    assert page_frame.attributes["anchored_frame"] is True
    assert any(run.image is not None for run in page_frame.runs)

    grouped_frame = document.blocks[2]
    assert grouped_frame.attributes["anchored_frame"] is True
    assert any(run.image is not None for run in grouped_frame.runs)

    text_frame = document.blocks[3]
    assert any(run.text == "Text inside a drawing frame" for run in text_frame.runs)

    embedded_chart = document.blocks[4]
    assert any(run.image is not None for run in embedded_chart.runs)


def test_table_cell_style_is_preserved(tmp_path: Path) -> None:
    odt = tmp_path / "styled-table.odt"
    make_odt(odt)

    document = load_book(odt)
    table = next(block for block in document.blocks if block.kind == "table")
    cell = table.children[0].children[0]

    assert cell.attributes["background_color"] == "#abcdef"
    assert cell.attributes["border"] == "0.5pt solid #000000"
    assert cell.children[0].text == "Cell value"
