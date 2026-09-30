from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class TextStyle:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    size: Optional[float] = None
    font: Optional[str] = None


@dataclass(slots=True)
class TextRun:
    text: str
    style: TextStyle = field(default_factory=TextStyle)


@dataclass(slots=True)
class TextBlock:
    kind: str
    runs: list[TextRun] = field(default_factory=list)
    align: Optional[str] = None
    style_name: Optional[str] = None

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(slots=True)
class BookDocument:
    path: Path
    title: str
    format: str
    blocks: list[TextBlock] = field(default_factory=list)
