"""Engine-neutral representation of an extracted document.

A document is an ordered sequence of blocks. Each block carries its source page
and, after ``assign_ids_and_sections``, the id of the section that contains it
(``"2.3.1"``), so that the origin of any piece of data can be cited precisely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

BlockType = Literal["heading", "paragraph", "list_item", "table", "figure", "caption"]


@dataclass
class Block:
    type: BlockType
    page: int  # numbered from 1
    text: str = ""
    level: int | None = None  # headings: 0 = document title, 1 = top section level
    rows: list[list[str]] | None = None  # tables only
    caption: str = ""
    image_bytes: bytes | None = None  # figures only (PNG), not serialized
    bbox: tuple[float, float, float, float] | None = None
    id: str = ""  # t001, f001, h001…
    section: str = ""  # id of the enclosing section
    file: str = ""  # side file (CSV or PNG), relative to the document folder

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("image_bytes", None)
        return {k: v for k, v in d.items() if v not in (None, "", [])}


@dataclass
class Section:
    id: str
    title: str
    level: int
    page: int
    children: list["Section"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "page": self.page,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class ExtractedDocument:
    title: str
    page_count: int
    engine: str
    blocks: list[Block] = field(default_factory=list)
    pdf_metadata: dict = field(default_factory=dict)
    native: dict | None = None  # native engine export (e.g. DoclingDocument), optional

    def assign_ids_and_sections(self) -> list[Section]:
        """Number headings, tables and figures, and attach each block to its section.

        Returns the section tree (original heading hierarchy).
        """
        roots: list[Section] = []
        stack: list[Section] = []
        counters: list[int] = []
        n = {"heading": 0, "table": 0, "figure": 0}
        for block in self.blocks:
            if block.type == "heading" and block.level == 0:
                block.id = "title"
                continue
            if block.type == "heading":
                level = max(1, block.level or 1)
                # Pop the stack back to the parent of this level.
                while stack and stack[-1].level >= level:
                    stack.pop()
                depth = len(stack)
                counters = counters[: depth + 1]
                while len(counters) < depth + 1:
                    counters.append(0)
                counters[depth] += 1
                sid = ".".join(str(c) for c in counters[: depth + 1])
                sec = Section(id=sid, title=block.text, level=level, page=block.page)
                (stack[-1].children if stack else roots).append(sec)
                stack.append(sec)
                n["heading"] += 1
                block.id = f"h{n['heading']:03d}"
                block.section = sid
                continue
            block.section = stack[-1].id if stack else ""
            if block.type in ("table", "figure"):
                n[block.type] += 1
                block.id = f"{block.type[0]}{n[block.type]:03d}"
        return roots
