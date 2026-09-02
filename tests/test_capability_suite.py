from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.capability_suite import IsolatedCapabilitySuite


class IsolatedCapabilitySuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parents[1] / "evals" / "isolated_capabilities_v1.json"

    def test_default_fixture_passes_seven_isolated_contracts(self) -> None:
        report = IsolatedCapabilitySuite().run(
            self.fixture,
            artifact_location=Path("report.json"),
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.gates), 7)
        self.assertEqual(
            {gate.capability for gate in report.gates},
            {
                "canonical_core",
                "mem0_boundary",
                "temporal_projection",
                "tags",
                "outcome_learning",
                "retrieval_channels",
                "evidence_packing",
            },
        )
        self.assertTrue(all(gate.checks for gate in report.gates))
        self.assertTrue(all(gate.passed for gate in report.gates))
        self.assertEqual(report.fixture_path, str(self.fixture))
        self.assertEqual(report.artifact_location, "report.json")
        self.assertFalse(report.feature_switches["real_mem0_model"])
        json.dumps(report.as_dict())

    def test_one_wrong_expectation_fails_only_its_own_gate(self) -> None:
        fixture = json.loads(self.fixture.read_text(encoding="utf-8"))
        fixture["evidence_packing"]["expected_total_selected"] = 5
        with tempfile.TemporaryDirectory() as directory:
            changed_fixture = Path(directory) / "changed.json"
            changed_fixture.write_text(json.dumps(fixture), encoding="utf-8")
            report = IsolatedCapabilitySuite().run(changed_fixture)

        failed = [gate.capability for gate in report.gates if not gate.passed]
        self.assertFalse(report.passed)
        self.assertEqual(failed, ["evidence_packing"])


if __name__ == "__main__":
    unittest.main()
