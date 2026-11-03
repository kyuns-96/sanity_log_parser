"""Real path access and explicitly synthetic OS metadata security checks."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from common.sanity_common import ReviewError
from common.sanity_submission_files import load_config, regular, write_json_file


class SubmissionFileAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sanity-file-access-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def read(self, path, maximum=1024):
        stream, info = regular(path, maximum)
        with stream:
            return stream.read(), info


    def test_parent_rename_and_symlink_swap_cannot_redirect_the_open_file(self):
        parent = self.root / 'parent'
        parent.mkdir()
        (parent / 'report').write_bytes(b'original synthetic evidence')
        foreign = self.root / 'foreign'
        foreign.mkdir()
        (foreign / 'report').write_bytes(b'wrong synthetic evidence')
        original_open = os.open
        swapped = False

        def rename_before_leaf(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == 'report' and kwargs.get('dir_fd') is not None and not swapped:
                parent.rename(self.root / 'moved')
                parent.symlink_to(foreign, target_is_directory=True)
                swapped = True
            return original_open(path, flags, *args, **kwargs)

        with patch.object(os, 'open', side_effect=rename_before_leaf):
            data, _ = self.read(parent / 'report')
        self.assertTrue(swapped)
        self.assertEqual(b'original synthetic evidence', data)


class SyntheticSubmissionMetadataTests(unittest.TestCase):
    """Mock only OS metadata; never substitute the production safety checks.

    These cases exercise validation logic where Android cannot create the real
    hardlink/root-owned fixtures. They do not prove kernel-level integration.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sanity-synthetic-metadata-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / 'client.json'
        self.config = {'schema_version': 2, 'transport': 'https',
                       'endpoint': 'https://synthetic.example.test', 'ca_file': None,
                       'timeout_seconds': 5, 'default_profile': 'synthetic-top'}
        write_json_file(self.config_path, self.config)

    def config_metadata(self, *, uid, permissions=0o600):
        original_stat = Path.stat

        def synthetic_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path != self.config_path:
                return info
            values = list(info)
            values[0] = stat.S_IFMT(info.st_mode) | permissions
            values[4] = uid
            return os.stat_result(values)

        return patch.object(Path, 'stat', autospec=True, side_effect=synthetic_stat)

    def test_synthetic_shared_or_unlinked_file_is_rejected_and_descriptor_closed(self):
        path = self.root / 'report'
        path.write_bytes(b'synthetic evidence')
        original_fstat = os.fstat
        for link_count in (0, 2, 3):
            descriptors = []

            def synthetic_fstat(descriptor):
                info = original_fstat(descriptor)
                values = list(info)
                values[3] = link_count
                descriptors.append(descriptor)
                return os.stat_result(values)

            with self.subTest(link_count=link_count):
                with patch.object(os, 'fstat', side_effect=synthetic_fstat), \
                        self.assertRaises(ReviewError) as error:
                    stream, _ = regular(path)
                    stream.close()
                self.assertEqual('UNSAFE_SUBMISSION_FILE', error.exception.code)
                self.assertEqual(1, len(descriptors))
                with self.assertRaises(OSError):
                    original_fstat(descriptors[0])
        self.assertEqual(1, path.stat().st_nlink)

        path = self.root / 'report'
        data = b'synthetic\r\n\x00\xff'
        path.write_bytes(data)
        original_fstat = os.fstat

        def synthetic_fstat(descriptor):
            values = list(original_fstat(descriptor))
            values[3] = 1
            return os.stat_result(values)

        with patch.object(os, 'fstat', side_effect=synthetic_fstat):
            stream, _ = regular(path)
            with stream:
                self.assertEqual(data, stream.read())


    def test_synthetic_configuration_enforces_root_ownership_permissions_and_https(self):
        for permissions in (0o600, 0o640):
            with self.subTest(permissions=permissions), \
                    self.config_metadata(uid=0, permissions=permissions):
                self.assertEqual(self.config, load_config(self.config_path))

        with self.config_metadata(uid=1000), self.assertRaises(ReviewError) as error:
            load_config(self.config_path)
        self.assertEqual('UNTRUSTED_SUBMISSION_CONFIG', error.exception.code)

        for permissions in (0o620, 0o602, 0o666):
            with self.subTest(permissions=permissions):
                with self.config_metadata(uid=0, permissions=permissions), \
                        self.assertRaises(ReviewError) as error:
                    load_config(self.config_path)
                self.assertEqual('UNTRUSTED_SUBMISSION_CONFIG', error.exception.code)

        write_json_file(self.config_path, {**self.config, 'endpoint': 'http://synthetic.example.test'})
        with self.config_metadata(uid=0), self.assertRaises(ReviewError) as error:
            load_config(self.config_path)
        self.assertEqual('SUBMISSION_CONFIG', error.exception.code)
