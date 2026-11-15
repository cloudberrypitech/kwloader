from pathlib import Path
from .models import BookDocument
from .odf_loader import load_odf
from .word_loader import load_word

SUPPORTED_EXTENSIONS = {".odt", ".fodt", ".ott", ".odm", ".odf", ".docx", ".dotx"}


def load_book(path: str | Path) -> BookDocument:
    """Load an ODF or Word document into the shared BookDocument model."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported book format: {extension or '(no extension)'}; supported formats: {supported}")
    return load_odf(path) if extension in {".odt", ".fodt", ".ott", ".odm", ".odf"} else load_word(path)
