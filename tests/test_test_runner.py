"""Quick/full scope selection must never turn missing tests or failures green."""
import unittest

import run_tests as runner


class TestRunnerTests(unittest.TestCase):
    def test_include_unions_overlapping_modules_and_methods_without_repeating_tests(self):
        name = 'tests.test_submission_files.SyntheticSubmissionMetadataTests'
        baseline = unittest.defaultTestLoader.loadTestsFromName(name + '.test_synthetic_configuration_enforces_root_ownership_permissions_and_https')
        expected = {test.id() for test in runner.test_cases(unittest.defaultTestLoader.loadTestsFromName(name))}
        suite = runner.include_tests(baseline, [name, name, next(iter(expected))])
        ids = [test.id() for test in runner.test_cases(suite)]
        self.assertEqual(expected, set(ids))
        self.assertEqual(len(expected), len(ids))
        shards = [test.id() for i in range(3) for test in runner.test_cases(runner.shard_suite(suite, i, 3))]
        self.assertCountEqual(ids, shards)
