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

The model preserves formatted text runs, paragraph alignment and properties, nested list/table blocks, metadata, page layout properties and embedded image bytes. `TextBlock.text` and the original `load_book()` API remain available for compatibility.

Only images embedded in the document package or inline data URIs are loaded. External linked images are not fetched.
