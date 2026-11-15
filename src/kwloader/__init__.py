"""kwloader: document extraction for ODF and Microsoft Word formats."""
from .loader import SUPPORTED_EXTENSIONS, load_book
from .models import BookDocument, ImageAsset, TextBlock, TextRun, TextStyle
from .odf_loader import ODFLoader, load_odf
from .word_loader import load_word

__version__ = "1.2.0"
__all__ = ["BookDocument", "ImageAsset", "TextBlock", "TextRun", "TextStyle",
           "SUPPORTED_EXTENSIONS", "load_book", "ODFLoader", "load_odf", "load_word"]
