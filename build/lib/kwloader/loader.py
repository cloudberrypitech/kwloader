from pathlib import Path

from .models import BookDocument
from .odf_loader import load_odf
from .word_loader import load_word


SUPPORTED_EXTENSIONS = {
    ".odt",
    ".fodt",
    ".odf",
    ".docx",
    ".dotx",
}


def load_book(path: str | Path) -> BookDocument:
    """
    Load a supported book and return a format-independent BookDocument.

    Supported:
        .odt, .fodt, .odf
        .docx, .dotx
    """
    path = Path(path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported book format: {extension or '(no extension)'}; "
            f"supported formats: {supported}"
        )

    if extension in {".odt", ".fodt", ".odf"}:
        return load_odf(path)

    return load_word(path)
