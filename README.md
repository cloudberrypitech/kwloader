# kwloader

`kwloader` converts ODT, FODT, OTT, ODM, ODF, DOCX and DOTX documents into a shared Python document model.

## Install

```bash
pip install kwloader
```

## Usage

```python
from kwloader import load_book

document = load_book("example.odt")
print(document.title, document.metadata)
for block in document.blocks:
    print(block.kind, block.text)
for image in document.images:
    print(image.name, image.mime_type, len(image.data))
```

The model preserves formatted text runs, inherited and default styles, paragraph properties, nested lists and tables, table-cell formatting, metadata, page layout/master-page data, and embedded image bytes. It supports inline and page-anchored drawing frames, grouped drawings, text boxes, and image references stored outside the usual `Pictures/` directory. `TextBlock.text` and the original `load_book()` API remain available for compatibility.

Only package-embedded images and inline data URIs are loaded; external linked images are not fetched. Complex OLE objects, charts, formulas and animated media are retained only as text/placeholders where they cannot be rendered by the reader.
