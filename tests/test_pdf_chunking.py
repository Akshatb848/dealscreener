"""PDF page-wise chunking tests."""

from unittest.mock import MagicMock, patch

from parser_layer import ParsedChunk, _parse_pdf_pages_native, parse_document


class TestPdfPageChunking:
    def test_native_pdf_pages_one_chunk_per_page(self, tmp_path):
        pdf = tmp_path / "deck.pdf"
        pdf.write_bytes(b"%PDF-1.4 minimal")

        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 3
        pages = []
        for i, text in enumerate(["Page one text", "Page two text", ""]):
            page = MagicMock()
            page.get_text.return_value = text
            pages.append(page)
        mock_doc.__getitem__.side_effect = lambda i: pages[i]

        with patch("fitz.open", return_value=mock_doc):
            chunks = _parse_pdf_pages_native(str(pdf), "deck.pdf")

        assert len(chunks) == 2
        assert chunks[0].page_index == 0
        assert chunks[1].page_index == 1
        assert chunks[0].extraction_method == "pymupdf_page"
        assert chunks[0].confidence_score >= 0.9

    def test_multipage_text_file_splits(self, tmp_path):
        f = tmp_path / "deck.txt"
        f.write_text("Page 1 content\fPage 2 content", encoding="utf-8")
        chunks = parse_document(str(f))
        assert len(chunks) >= 1
        assert all(isinstance(c, ParsedChunk) for c in chunks)
