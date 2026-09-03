import unittest
from io import StringIO

from data_retrieval.ingestion.chunker import TextChunker


class TextChunkerTests(unittest.TestCase):
    def test_streaming_chunks_match_in_memory_chunks(self) -> None:
        text = (
            "First paragraph has a useful natural boundary.\n\n"
            "Second paragraph is deliberately repeated to cross windows. " * 40
        )
        chunker = TextChunker(max_chars=180, overlap_chars=30)

        streamed = tuple(chunker.iter_stream(StringIO(text), read_size=181))

        self.assertEqual(streamed, chunker.split(text))

    def test_preserves_order_offsets_and_overlap(self) -> None:
        text = " ".join(f"word-{index}" for index in range(100))
        chunks = TextChunker(max_chars=120, overlap_chars=20).split(text)

        self.assertGreater(len(chunks), 1)
        self.assertEqual([chunk.position for chunk in chunks], list(range(len(chunks))))
        for chunk in chunks:
            self.assertEqual(text[chunk.char_start : chunk.char_end], chunk.text)
            self.assertLessEqual(len(chunk.text), 120)
        for previous, current in zip(chunks, chunks[1:], strict=False):
            self.assertLess(current.char_start, previous.char_end)

    def test_prefers_paragraph_boundary_over_later_space(self) -> None:
        text = (
            "Alice leads Project Helios.\n\n"
            "OpenAI provides the inference service used by Project Helios.\n\n"
            "The Mac Mini runs overnight evaluation jobs for Project Helios."
        )

        chunks = TextChunker(max_chars=100, overlap_chars=0).split(text)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(
            chunks[0].text,
            "Alice leads Project Helios.\n\n"
            "OpenAI provides the inference service used by Project Helios.",
        )
        self.assertTrue(chunks[1].text.startswith("The Mac Mini"))

    def test_rejects_empty_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "text cannot be empty"):
            TextChunker().split("   ")


if __name__ == "__main__":
    unittest.main()
