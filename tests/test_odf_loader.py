import zipfile
from pathlib import Path

from kwloader import ImageAsset, load_book

NS = '''
xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
xmlns:xlink="http://www.w3.org/1999/xlink"
xmlns:dc="http://purl.org/dc/elements/1.1/"
'''


def make_odt(path: Path):
    content = f'''<office:document-content {NS}>
      <office:automatic-styles>
        <style:style style:name="Strong" style:family="text">
          <style:text-properties fo:font-weight="bold" fo:color="#cc0000" fo:font-size="16pt"/>
        </style:style>
        <style:style style:name="Centered" style:family="paragraph">
          <style:paragraph-properties fo:text-align="center" fo:margin-bottom="6pt"/>
          <style:text-properties fo:font-family="Liberation Serif"/>
        </style:style>
      </office:automatic-styles>
      <office:body><office:text>
        <text:h text:outline-level="1">Sample heading</text:h>
        <text:p text:style-name="Centered">Normal <text:span text:style-name="Strong">bold red</text:span> text</text:p>
        <text:list><text:list-item><text:p>List item</text:p></text:list-item></text:list>
        <table:table table:name="Sample"><table:table-row><table:table-cell><text:p>Cell A</text:p></table:table-cell><table:table-cell><text:p>Cell B</text:p></table:table-cell></table:table-row></table:table>
        <text:p><draw:frame svg:width="2cm" svg:height="1cm"><draw:image xlink:href="Pictures/test.png"/></draw:frame></text:p>
      </office:text></office:body>
    </office:document-content>'''
    styles = f'''<office:document-styles {NS}><office:styles><style:default-style style:family="text"><style:text-properties fo:font-family="Arial"/></style:default-style></office:styles></office:document-styles>'''
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.xml", content)
        z.writestr("styles.xml", styles)
        z.writestr("meta.xml", f'<office:document-meta {NS}><office:meta><dc:title>ODT Test</dc:title></office:meta></office:document-meta>')
        z.writestr("Pictures/test.png", b"not-a-real-png-but-embedded")


def test_odt_parses_styles_lists_tables_and_images(tmp_path):
    path = tmp_path / "sample.odt"
    make_odt(path)
    doc = load_book(path)
    assert doc.title == "ODT Test"
    assert any(b.kind == "heading" for b in doc.blocks)
    paragraph = next(b for b in doc.blocks if b.kind == "paragraph" and "bold red" in b.text)
    assert paragraph.align == "center"
    strong = next(run for run in paragraph.runs if run.text == "bold red")
    assert strong.style.bold is True
    assert strong.style.color == "#cc0000"
    assert any(b.kind == "list" for b in doc.blocks)
    assert any(b.kind == "table" for b in doc.blocks)
    assert len(doc.images) == 1
    assert doc.images[0].data.startswith(b"not-a-real")
    assert any(run.image for b in doc.blocks for run in b.runs)


def test_supported_extensions_and_text_api(tmp_path):
    path = tmp_path / "sample.odt"
    make_odt(path)
    doc = load_book(path)
    assert ".odt" in __import__("kwloader").SUPPORTED_EXTENSIONS
    assert isinstance(doc.blocks[0].text, str)


def test_fodt_is_supported(tmp_path):
    path = tmp_path / "sample.fodt"
    path.write_text(f'''<office:document {NS}><office:body><office:text>
      <text:p>Flat ODT text</text:p>
    </office:text></office:body></office:document>''', encoding="utf-8")
    doc = load_book(path)
    assert doc.blocks[0].text == "Flat ODT text"
