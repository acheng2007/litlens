import pymupdf
import pytest

from litlens.ingestion.arxiv import parse_feed
from litlens.ingestion.chunking import chunk_pages
from litlens.ingestion.pdf import InvalidPDF, extract_pdf


def test_sections_do_not_leak_backwards_and_continue_across_pages():
    chunks = chunk_pages(
        [(1, "METHODS\n\nFirst method.\n\nRESULTS\n\nNew result."), (2, "Continuation.")]
    )
    assert [(c.section, c.page) for c in chunks] == [("METHODS", 1), ("RESULTS", 1), ("RESULTS", 2)]


def test_long_paragraphs_bounded():
    chunks = chunk_pages([(1, "word " * 2000)], max_chars=100, overlap=10)
    assert len(chunks) > 10
    assert all(0 < len(c.text) <= 100 for c in chunks)
    with pytest.raises(ValueError):
        chunk_pages([], 10, 10)


def test_numbered_heading_split_over_lines():
    chunks = chunk_pages(
        [
            (
                2,
                "1\nIntroduction\n\nThe Transformer relies on attention.\n\n2\nBackground\n\nEarlier models use recurrence.",
            )
        ]
    )
    assert [c.section for c in chunks] == ["1 Introduction", "2 Background"]


def test_real_pdf_and_empty_pdf(tmp_path):
    path = tmp_path / "test.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "METHODS")
        page.insert_text((72, 120), "Hybrid retrieval combines keyword and vector search.")
        doc.save(path)
    chunks = extract_pdf(path)
    assert chunks[0].page == 1 and chunks[0].section == "METHODS"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(tmp_path / "empty.pdf")
    with pytest.raises(InvalidPDF, match="OCR"):
        extract_pdf(tmp_path / "empty.pdf")


def test_arxiv_parser():
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title></entry></feed>'
    assert parse_feed(feed)[0].arxiv_id == "1706.03762v7"
    with pytest.raises(ValueError):
        parse_feed(feed.replace(b"arxiv.org", b"evil.example"))


def test_extraction_handles_math_glyphs_nul_and_punctuation(monkeypatch):
    from litlens.retrieval.embeddings import embed_many

    class Page:
        def get_text(self, *args, **kwargs):
            return [
                (0, 0, 0, 0, text, 0, 0)
                for text in [
                    "Abstract",
                    "A ﬁnding\x00with mathematical 𝑥 and ℎ.",
                ]
            ]

    class NoisePage(Page):
        def get_text(self, *args, **kwargs):
            return [(0, 0, 0, 0, "... !", 0, 0)]

    class PDF:
        is_pdf, needs_pass = True, False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __len__(self):
            return 2

        def __iter__(self):
            return iter([Page(), NoisePage()])

    monkeypatch.setattr(pymupdf, "open", lambda _: PDF())
    chunks = extract_pdf("unused.pdf")
    assert len(chunks) == 1
    assert chunks[0].text == "A finding with mathematical x and h."
    assert chunks[0].page == 1
    assert len(embed_many([c.text for c in chunks])) == 1
