from docx import Document
from docx.shared import Pt
from kwloader import load_book


def test_docx_text_and_styles(tmp_path):
    path = tmp_path / "sample.docx"
    document = Document()
    heading = document.add_heading("Test heading", level=1)
    paragraph = document.add_paragraph()
    run = paragraph.add_run("bold text")
    run.bold = True
    run.font.size = Pt(15)
    document.save(path)

    loaded = load_book(path)
    assert loaded.title == "Test heading"
    assert loaded.blocks[0].kind == "heading"
    bold = next(run for block in loaded.blocks for run in block.runs if run.text == "bold text")
    assert bold.style.bold
    assert bold.style.size == 15
