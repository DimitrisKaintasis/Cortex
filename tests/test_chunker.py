import unittest

from data_retrieval.ingestion.chunker import TextChunker


class TextChunkerTests(unittest.TestCase):
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

    def test_rejects_empty_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "text cannot be empty"):
            TextChunker().split("   ")


if __name__ == "__main__":
    unittest.main()
