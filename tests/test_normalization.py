import unittest

from data_retrieval.tagging.normalization import deduplicate_tags, normalize_tag


class TagNormalizationTests(unittest.TestCase):
    def test_normalizes_case_separators_and_punctuation(self) -> None:
        self.assertEqual(normalize_tag(" API_Calls! "), "api calls")

    def test_deduplicates_by_canonical_form(self) -> None:
        tags = deduplicate_tags(("API Calls", "api_calls", "Retrieval"))
        self.assertEqual(tags, (("api calls", "API Calls"), ("retrieval", "Retrieval")))


if __name__ == "__main__":
    unittest.main()
