import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import file_ops  # noqa: E402


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FileOpsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.src = os.path.join(self.root, "src")
        self.dst = os.path.join(self.root, "dst")
        os.makedirs(self.src)
        os.makedirs(self.dst)

    def tearDown(self):
        self._tmp.cleanup()


class UniquePathTests(FileOpsTestCase):
    def test_returns_same_when_free(self):
        p = os.path.join(self.root, "a.txt")
        self.assertEqual(file_ops.unique_path(p), p)

    def test_file_gets_numbered_before_extension(self):
        p = os.path.join(self.root, "a.txt")
        write(p)
        self.assertEqual(file_ops.unique_path(p), os.path.join(self.root, "a (2).txt"))
        write(os.path.join(self.root, "a (2).txt"))
        self.assertEqual(file_ops.unique_path(p), os.path.join(self.root, "a (3).txt"))

    def test_folder_with_dot_keeps_whole_name(self):
        p = os.path.join(self.root, "v1.2")
        os.makedirs(p)
        self.assertEqual(file_ops.unique_path(p), os.path.join(self.root, "v1.2 (2)"))


class CopyTests(FileOpsTestCase):
    def test_copy_file(self):
        write(os.path.join(self.src, "a.txt"), "hello")
        dest = file_ops.copy_item(os.path.join(self.src, "a.txt"), self.dst)
        self.assertEqual(dest, os.path.join(self.dst, "a.txt"))
        self.assertEqual(read(dest), "hello")
        self.assertTrue(os.path.exists(os.path.join(self.src, "a.txt")))

    def test_copy_does_not_overwrite_existing(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.copy_item(os.path.join(self.src, "a.txt"), self.dst)
        self.assertEqual(read(os.path.join(self.dst, "a.txt")), "old")
        self.assertEqual(dest, os.path.join(self.dst, "a (2).txt"))
        self.assertEqual(read(dest), "new")

    def test_copy_into_same_folder_makes_numbered_copy(self):
        write(os.path.join(self.src, "a.txt"), "hello")
        dest = file_ops.copy_item(os.path.join(self.src, "a.txt"), self.src)
        self.assertEqual(dest, os.path.join(self.src, "a (2).txt"))
        self.assertEqual(read(os.path.join(self.src, "a.txt")), "hello")

    def test_copy_folder_recursively(self):
        write(os.path.join(self.src, "d", "sub", "f.txt"), "deep")
        dest = file_ops.copy_item(os.path.join(self.src, "d"), self.dst)
        self.assertEqual(read(os.path.join(dest, "sub", "f.txt")), "deep")

    def test_copy_folder_into_itself_is_rejected(self):
        folder = os.path.join(self.src, "d")
        os.makedirs(os.path.join(folder, "child"))
        with self.assertRaises(ValueError):
            file_ops.copy_item(folder, folder)
        with self.assertRaises(ValueError):
            file_ops.copy_item(folder, os.path.join(folder, "child"))

    def test_copy_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            file_ops.copy_item(os.path.join(self.src, "nope"), self.dst)


class MoveTests(FileOpsTestCase):
    def test_move_file(self):
        write(os.path.join(self.src, "a.txt"), "hello")
        dest = file_ops.move_item(os.path.join(self.src, "a.txt"), self.dst)
        self.assertEqual(read(dest), "hello")
        self.assertFalse(os.path.exists(os.path.join(self.src, "a.txt")))

    def test_move_does_not_overwrite_existing(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.move_item(os.path.join(self.src, "a.txt"), self.dst)
        self.assertEqual(read(os.path.join(self.dst, "a.txt")), "old")
        self.assertEqual(read(dest), "new")

    def test_move_to_same_folder_is_noop(self):
        p = os.path.join(self.src, "a.txt")
        write(p, "hello")
        self.assertEqual(file_ops.move_item(p, self.src), p)
        self.assertEqual(read(p), "hello")

    def test_move_folder_into_itself_is_rejected(self):
        folder = os.path.join(self.src, "d")
        os.makedirs(os.path.join(folder, "child"))
        with self.assertRaises(ValueError):
            file_ops.move_item(folder, os.path.join(folder, "child"))
        self.assertTrue(os.path.isdir(folder))

    def test_move_root_is_rejected(self):
        with self.assertRaises(ValueError):
            file_ops.move_item(os.path.abspath(os.sep), self.dst)


class DeleteTests(FileOpsTestCase):
    def test_permanent_delete_file_and_folder(self):
        f = os.path.join(self.src, "a.txt")
        d = os.path.join(self.src, "d")
        write(f)
        write(os.path.join(d, "x.txt"))
        file_ops.delete_item(f, permanent=True)
        file_ops.delete_item(d, permanent=True)
        self.assertFalse(os.path.exists(f))
        self.assertFalse(os.path.exists(d))

    def test_delete_root_is_rejected(self):
        with self.assertRaises(ValueError):
            file_ops.delete_item(os.path.abspath(os.sep), permanent=True)

    def test_trash_without_send2trash_raises(self):
        f = os.path.join(self.src, "a.txt")
        write(f)
        with mock.patch.object(file_ops, "_send2trash", None):
            with self.assertRaises(RuntimeError):
                file_ops.delete_item(f, permanent=False)
        self.assertTrue(os.path.exists(f))  # 何も消えていない

    def test_trash_uses_send2trash(self):
        f = os.path.join(self.src, "a.txt")
        write(f)
        fake = mock.Mock()
        with mock.patch.object(file_ops, "_send2trash", fake):
            file_ops.delete_item(f, permanent=False)
        fake.assert_called_once_with(os.path.normpath(f))


class RunBatchTests(FileOpsTestCase):
    def test_continues_after_failure_and_collects_errors(self):
        write(os.path.join(self.src, "a.txt"))
        write(os.path.join(self.src, "c.txt"))
        paths = [
            os.path.join(self.src, "a.txt"),
            os.path.join(self.src, "missing.txt"),
            os.path.join(self.src, "c.txt"),
        ]
        result = file_ops.run_batch(paths, lambda p: file_ops.copy_item(p, self.dst))
        self.assertEqual(result.success_count, 2)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0][0], paths[1])
        self.assertFalse(result.cancelled)

    def test_cancel_stops_before_next_item(self):
        for name in ("a", "b", "c"):
            write(os.path.join(self.src, name))
        paths = [os.path.join(self.src, n) for n in ("a", "b", "c")]
        done = []

        def action(p):
            done.append(p)
            return p

        result = file_ops.run_batch(paths, action, is_cancelled=lambda: len(done) >= 1)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(done), 1)

    def test_progress_is_reported_before_each_item(self):
        for name in ("a", "b"):
            write(os.path.join(self.src, name))
        paths = [os.path.join(self.src, n) for n in ("a", "b")]
        seen = []
        file_ops.run_batch(paths, lambda p: p, progress=lambda i, total, name: seen.append((i, total, name)))
        self.assertEqual(seen, [(0, 2, "a"), (1, 2, "b")])


class MiscTests(unittest.TestCase):
    def test_is_root(self):
        self.assertTrue(file_ops.is_root(os.path.abspath(os.sep)))
        self.assertFalse(file_ops.is_root(tempfile.gettempdir()))

    def test_format_size(self):
        self.assertEqual(file_ops.format_size(10), "10 bytes")
        self.assertEqual(file_ops.format_size(2048), "2.00 KB")
        self.assertEqual(file_ops.format_size(5 * 1024 ** 2), "5.00 MB")
        self.assertEqual(file_ops.format_size(3 * 1024 ** 3), "3.00 GB")


if __name__ == "__main__":
    unittest.main()
