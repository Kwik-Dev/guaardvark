"""Every part of an oversized markdown section keeps its section: the parts
after the first used to load without heading_path, so their passages had no
section in citations and outlines (contextual_prepender's "Section:" line)."""
from types import SimpleNamespace

from backend.utils import markdown_sections as ms


def test_every_part_of_a_long_section_names_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "MAX_SECTION_CHARS", 200)
    body = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(6))
    md = tmp_path / "guide.md"
    md.write_text(f"# Guide\n\n## Storage\n\n{body}\n\n## Search\n\nshort\n")
    docs = ms.load_documents(str(md), "guide.md", lambda text, metadata: SimpleNamespace(text=text, metadata=metadata))
    storage = [d for d in docs if "Paragraph" in d.text]
    assert len(storage) > 1
    for d in storage:
        assert d.metadata["heading_path"] == "Guide > Storage"
        assert d.metadata["heading"] == "Storage"
    # The heading line is still written once, at the first part only.
    assert storage[0].text.startswith("## Storage") and not storage[1].text.startswith("## Storage")
    assert [d.metadata["section_part"] for d in storage] == list(range(len(storage)))
    assert docs[-1].metadata["heading_path"] == "Guide > Search" and "section_part" not in docs[-1].metadata
