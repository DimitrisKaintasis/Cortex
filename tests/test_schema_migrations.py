import unittest

from data_retrieval.storage.migrations import (
    BASELINE_SCHEMA,
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    UnsupportedSchemaVersion,
    pending_migrations,
)


class SchemaMigrationPlanTests(unittest.TestCase):
    def test_migration_versions_and_names_are_unique_and_contiguous(self) -> None:
        versions = tuple(migration.version for migration in MIGRATIONS)
        names = tuple(migration.name for migration in MIGRATIONS)

        self.assertEqual(versions, tuple(range(1, CURRENT_SCHEMA_VERSION + 1)))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(MIGRATIONS[0], BASELINE_SCHEMA)

    def test_pending_plan_is_ordered_and_current_version_is_a_no_op(self) -> None:
        self.assertEqual(pending_migrations(0), MIGRATIONS)
        self.assertEqual(pending_migrations(CURRENT_SCHEMA_VERSION), ())

    def test_future_version_fails_closed(self) -> None:
        with self.assertRaisesRegex(UnsupportedSchemaVersion, "newer than supported"):
            pending_migrations(CURRENT_SCHEMA_VERSION + 1)


if __name__ == "__main__":
    unittest.main()
