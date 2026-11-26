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



class CreateTests(FileOpsTestCase):
    def test_create_folder(self):
        p = file_ops.create_folder(self.dst)
        self.assertTrue(os.path.isdir(p))
        self.assertEqual(os.path.basename(p), "新しいフォルダ")

    def test_create_folder_unique_name(self):
        p1 = file_ops.create_folder(self.dst)
        p2 = file_ops.create_folder(self.dst)
        self.assertNotEqual(p1, p2)
        self.assertEqual(os.path.basename(p2), "新しいフォルダ (2)")
        self.assertTrue(os.path.isdir(p1))

    def test_create_text_file_is_empty(self):
        p = file_ops.create_text_file(self.dst)
        self.assertTrue(os.path.isfile(p))
        self.assertEqual(os.path.basename(p), "新しいテキストファイル.txt")
        self.assertEqual(read(p), "")

    def test_create_text_file_unique_name(self):
        file_ops.create_text_file(self.dst)
        p2 = file_ops.create_text_file(self.dst)
        self.assertEqual(os.path.basename(p2), "新しいテキストファイル (2).txt")

    def test_create_custom_name(self):
        p = file_ops.create_folder(self.dst, "作業用")
        self.assertEqual(os.path.basename(p), "作業用")

    def test_create_in_missing_dir(self):
        with self.assertRaises(FileNotFoundError):
            file_ops.create_folder(os.path.join(self.root, "nope"))
        with self.assertRaises(FileNotFoundError):
            file_ops.create_text_file(os.path.join(self.root, "nope"))

    def test_invalid_names_are_rejected(self):
        for bad in ("", "   ", ".", "..", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "name.", "CON", "con.txt", "LPT1", "aux.py"):
            with self.assertRaises(ValueError, msg=bad):
                file_ops.create_folder(self.dst, bad)
            with self.assertRaises(ValueError, msg=bad):
                file_ops.create_text_file(self.dst, bad)


class ChunkedCopyTests(FileOpsTestCase):
    def make_big(self, name="big.bin", size=3 * 1024 * 1024 + 123):
        p = os.path.join(self.src, name)
        with open(p, "wb") as f:
            f.write(os.urandom(size))
        return p

    def test_copy_file_content_and_progress(self):
        src = self.make_big()
        seen = []
        dest = file_ops.copy_item(src, self.dst, progress=lambda d, total: seen.append((d, total)))
        with open(src, "rb") as f:
            expected = f.read()
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), expected)
        self.assertEqual(seen[-1], (os.path.getsize(src), os.path.getsize(src)))

    def test_copy_preserves_mtime(self):
        src = self.make_big()
        os.utime(src, (1_600_000_000, 1_600_000_000))
        dest = file_ops.copy_item(src, self.dst)
        self.assertEqual(int(os.path.getmtime(dest)), 1_600_000_000)

    def test_cancel_leaves_no_file(self):
        src = self.make_big()
        calls = []

        def cancel_after_first():
            calls.append(1)
            return len(calls) > 1

        with self.assertRaises(file_ops.OperationCancelled):
            file_ops.copy_item(src, self.dst, is_cancelled=cancel_after_first)
        self.assertEqual(os.listdir(self.dst), [])  # 一時ファイルも残らない

    def test_copy_folder_with_progress(self):
        write(os.path.join(self.src, "d", "a.txt"), "aaa")
        write(os.path.join(self.src, "d", "sub", "b.txt"), "bbb")
        seen = []
        dest = file_ops.copy_item(os.path.join(self.src, "d"), self.dst,
                                  progress=lambda d, total: seen.append((d, total)))
        self.assertEqual(read(os.path.join(dest, "a.txt")), "aaa")
        self.assertEqual(read(os.path.join(dest, "sub", "b.txt")), "bbb")
        self.assertEqual(seen[-1][0], seen[-1][1])

    def test_cancel_folder_copy_removes_partial(self):
        write(os.path.join(self.src, "d", "a.txt"), "aaa")
        with self.assertRaises(file_ops.OperationCancelled):
            file_ops.copy_item(os.path.join(self.src, "d"), self.dst, is_cancelled=lambda: True)
        self.assertEqual(os.listdir(self.dst), [])

    def test_run_batch_mid_file_cancel_is_not_a_failure(self):
        src = self.make_big()
        result = file_ops.run_batch(
            [src],
            lambda p: file_ops.copy_item(p, self.dst, is_cancelled=lambda: True),
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(result.failed, [])
        self.assertEqual(result.success_count, 0)




class V111RegressionTests(unittest.TestCase):
    """v1.1.1 の修正に対する回帰テスト。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ngf-test-111-")

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        file_ops._rmtree(self.tmp) if os.path.exists(self.tmp) else None

    def test_format_size_boundaries(self):
        self.assertEqual(file_ops.format_size(0), "0 bytes")
        self.assertEqual(file_ops.format_size(1023), "1023 bytes")
        self.assertEqual(file_ops.format_size(1024), "1.00 KB")
        self.assertEqual(file_ops.format_size(1024 ** 2), "1.00 MB")
        self.assertEqual(file_ops.format_size(1024 ** 3), "1.00 GB")

    def test_validate_name_length(self):
        self.assertEqual(file_ops.validate_item_name("a" * 255), "a" * 255)
        with self.assertRaises(ValueError):
            file_ops.validate_item_name("a" * 256)

    def test_delete_readonly_file(self):
        p = os.path.join(self.tmp, "ro.txt")
        with open(p, "w") as f:
            f.write("x")
        os.chmod(p, 0o444)
        file_ops.delete_item(p, permanent=True)
        self.assertFalse(os.path.exists(p))

    def test_delete_readonly_tree(self):
        d = os.path.join(self.tmp, "tree")
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        p = os.path.join(sub, "ro.txt")
        with open(p, "w") as f:
            f.write("x")
        os.chmod(p, 0o444)
        os.chmod(sub, 0o555)
        file_ops.delete_item(d, permanent=True)
        self.assertFalse(os.path.exists(d))



class ConflictPolicyTests(FileOpsTestCase):
    """v1.2.0: 名前衝突時のポリシー (置換 / スキップ / 両方残す / マージ)。"""

    def test_copy_default_keeps_both(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.copy_item(os.path.join(self.src, "a.txt"), self.dst)
        self.assertEqual(os.path.basename(dest), "a (2).txt")

    def test_copy_overwrite_replaces_file(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.copy_item(
            os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_OVERWRITE
        )
        self.assertEqual(dest, os.path.join(self.dst, "a.txt"))
        self.assertEqual(read(dest), "new")

    def test_copy_overwrite_replaces_folder(self):
        write(os.path.join(self.src, "d", "new.txt"), "new")
        write(os.path.join(self.dst, "d", "old.txt"), "old")
        dest = file_ops.copy_item(
            os.path.join(self.src, "d"), self.dst, on_conflict=file_ops.CONFLICT_OVERWRITE
        )
        self.assertTrue(os.path.exists(os.path.join(dest, "new.txt")))
        self.assertFalse(os.path.exists(os.path.join(dest, "old.txt")))

    def test_copy_skip_raises_and_copies_nothing(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        with self.assertRaises(file_ops.ItemSkipped):
            file_ops.copy_item(
                os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_SKIP
            )
        self.assertEqual(read(os.path.join(self.dst, "a.txt")), "old")

    def test_copy_skip_without_conflict_works(self):
        write(os.path.join(self.src, "a.txt"), "new")
        dest = file_ops.copy_item(
            os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_SKIP
        )
        self.assertEqual(read(dest), "new")

    def test_copy_into_same_folder_never_overwrites_itself(self):
        p = os.path.join(self.src, "a.txt")
        write(p, "hello")
        dest = file_ops.copy_item(p, self.src, on_conflict=file_ops.CONFLICT_OVERWRITE)
        self.assertEqual(read(p), "hello")
        self.assertEqual(read(dest), "hello")
        self.assertNotEqual(dest, p)

    def test_copy_merge_folders(self):
        write(os.path.join(self.src, "d", "a.txt"), "from-src")
        write(os.path.join(self.src, "d", "sub", "c.txt"), "deep")
        write(os.path.join(self.dst, "d", "a.txt"), "from-dst")
        write(os.path.join(self.dst, "d", "b.txt"), "keep")
        dest = file_ops.copy_item(
            os.path.join(self.src, "d"), self.dst, on_conflict=file_ops.CONFLICT_MERGE
        )
        self.assertEqual(dest, os.path.join(self.dst, "d"))
        self.assertEqual(read(os.path.join(dest, "a.txt")), "from-src")  # 中のファイルは上書き
        self.assertEqual(read(os.path.join(dest, "b.txt")), "keep")      # 宛先だけのファイルは残る
        self.assertEqual(read(os.path.join(dest, "sub", "c.txt")), "deep")

    def test_merge_policy_on_files_falls_back_to_overwrite(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.copy_item(
            os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_MERGE
        )
        self.assertEqual(read(dest), "new")

    def test_move_overwrite(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        dest = file_ops.move_item(
            os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_OVERWRITE
        )
        self.assertEqual(read(dest), "new")
        self.assertFalse(os.path.exists(os.path.join(self.src, "a.txt")))

    def test_move_skip_keeps_source(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.dst, "a.txt"), "old")
        with self.assertRaises(file_ops.ItemSkipped):
            file_ops.move_item(
                os.path.join(self.src, "a.txt"), self.dst, on_conflict=file_ops.CONFLICT_SKIP
            )
        self.assertTrue(os.path.exists(os.path.join(self.src, "a.txt")))
        self.assertEqual(read(os.path.join(self.dst, "a.txt")), "old")

    def test_move_merge_folders(self):
        write(os.path.join(self.src, "d", "a.txt"), "from-src")
        write(os.path.join(self.dst, "d", "b.txt"), "keep")
        dest = file_ops.move_item(
            os.path.join(self.src, "d"), self.dst, on_conflict=file_ops.CONFLICT_MERGE
        )
        self.assertEqual(read(os.path.join(dest, "a.txt")), "from-src")
        self.assertEqual(read(os.path.join(dest, "b.txt")), "keep")
        self.assertFalse(os.path.exists(os.path.join(self.src, "d")))

    def test_chunked_copy_overwrite_for_big_file(self):
        src = os.path.join(self.src, "big.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024 + 7))
        write(os.path.join(self.dst, "big.bin"), "old")
        seen = []
        dest = file_ops.copy_item(
            src,
            self.dst,
            progress=lambda d, t: seen.append((d, t)),
            on_conflict=file_ops.CONFLICT_OVERWRITE,
        )
        self.assertEqual(dest, os.path.join(self.dst, "big.bin"))
        self.assertEqual(os.path.getsize(dest), 2 * 1024 * 1024 + 7)
        self.assertEqual(seen[-1], (2 * 1024 * 1024 + 7, 2 * 1024 * 1024 + 7))

    def test_run_batch_records_skips_separately(self):
        write(os.path.join(self.src, "a.txt"), "new")
        write(os.path.join(self.src, "b.txt"), "bee")
        write(os.path.join(self.dst, "a.txt"), "old")
        paths = [os.path.join(self.src, "a.txt"), os.path.join(self.src, "b.txt")]
        result = file_ops.run_batch(
            paths,
            lambda p: file_ops.copy_item(p, self.dst, on_conflict=file_ops.CONFLICT_SKIP),
        )
        self.assertEqual(result.skipped, [paths[0]])
        self.assertEqual(result.failed, [])
        self.assertEqual(result.success_count, 1)


class FolderSizeTests(FileOpsTestCase):
    def test_folder_total_size_sums_recursively(self):
        write(os.path.join(self.src, "d", "a.txt"), "12345")
        write(os.path.join(self.src, "d", "sub", "b.txt"), "123")
        self.assertEqual(file_ops.folder_total_size(os.path.join(self.src, "d")), 8)

    def test_folder_total_size_empty_folder(self):
        os.makedirs(os.path.join(self.src, "empty"))
        self.assertEqual(file_ops.folder_total_size(os.path.join(self.src, "empty")), 0)


class ZipTests(FileOpsTestCase):
    """v1.2.0: ZIP 圧縮 / 展開。"""

    def test_create_and_extract_file(self):
        write(os.path.join(self.src, "a.txt"), "hello zip")
        archive = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst)
        self.assertEqual(os.path.basename(archive), "a.zip")
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        created = file_ops.extract_zip(archive, out)
        self.assertEqual(created, [os.path.join(out, "a.txt")])
        self.assertEqual(read(os.path.join(out, "a.txt")), "hello zip")

    def test_create_zip_folder_recursion_and_name(self):
        write(os.path.join(self.src, "d", "x.txt"), "x")
        write(os.path.join(self.src, "d", "sub", "y.txt"), "y")
        archive = file_ops.create_zip([os.path.join(self.src, "d")], self.dst)
        self.assertEqual(os.path.basename(archive), "d.zip")
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        created = file_ops.extract_zip(archive, out)
        self.assertEqual(created, [os.path.join(out, "d")])
        self.assertEqual(read(os.path.join(out, "d", "sub", "y.txt")), "y")

    def test_create_zip_multiple_items(self):
        write(os.path.join(self.src, "a.txt"), "a")
        write(os.path.join(self.src, "b.txt"), "b")
        archive = file_ops.create_zip(
            [os.path.join(self.src, "a.txt"), os.path.join(self.src, "b.txt")], self.dst
        )
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        file_ops.extract_zip(archive, out)
        self.assertEqual(read(os.path.join(out, "a.txt")), "a")
        self.assertEqual(read(os.path.join(out, "b.txt")), "b")

    def test_create_zip_avoids_overwrite(self):
        write(os.path.join(self.src, "a.txt"), "a")
        first = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst)
        second = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst)
        self.assertNotEqual(first, second)
        self.assertEqual(os.path.basename(second), "a (2).zip")

    def test_create_zip_custom_name(self):
        write(os.path.join(self.src, "a.txt"), "a")
        archive = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst, archive_name="まとめ")
        self.assertEqual(os.path.basename(archive), "まとめ.zip")

    def test_create_zip_empty_paths(self):
        with self.assertRaises(ValueError):
            file_ops.create_zip([], self.dst)

    def test_create_zip_cancel_removes_partial(self):
        write(os.path.join(self.src, "a.txt"), "a")
        with self.assertRaises(file_ops.OperationCancelled):
            file_ops.create_zip(
                [os.path.join(self.src, "a.txt")], self.dst, is_cancelled=lambda: True
            )
        self.assertEqual(os.listdir(self.dst), [])

    def test_create_zip_progress_reaches_total(self):
        write(os.path.join(self.src, "a.txt"), "12345")
        seen = []
        file_ops.create_zip(
            [os.path.join(self.src, "a.txt")], self.dst, progress=lambda d, t: seen.append((d, t))
        )
        self.assertEqual(seen[-1], (5, 5))

    def test_extract_blocks_zip_slip(self):
        import zipfile

        evil = os.path.join(self.root, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.txt", "bad")
            zf.writestr("ok.txt", "good")
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        created = file_ops.extract_zip(evil, out)
        self.assertEqual(created, [os.path.join(out, "ok.txt")])
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape.txt")))
        self.assertEqual(read(os.path.join(out, "ok.txt")), "good")

    def test_extract_overwrites_existing_file(self):
        write(os.path.join(self.src, "a.txt"), "new")
        archive = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst)
        write(os.path.join(self.dst, "a.txt"), "old")
        file_ops.extract_zip(archive, self.dst)
        self.assertEqual(read(os.path.join(self.dst, "a.txt")), "new")



class ArchiveBrowseTests(FileOpsTestCase):
    """v1.3.0: ZIP の中身の参照 (アーカイブ表示用)。"""

    def _make_archive(self):
        import zipfile

        archive = os.path.join(self.root, "sample.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("root.txt", "root-content")
            zf.writestr("sub/inner.txt", "inner-content")
            zf.writestr("sub/deep/deeper.txt", "deep-content")
            zf.writestr("sub2/other.txt", "other")
            zf.writestr("emptydir/", "")
        return archive

    def test_list_root_level(self):
        entries = file_ops.list_zip_entries(self._make_archive())
        by_name = {e.name: e for e in entries}
        self.assertEqual(set(by_name), {"root.txt", "sub", "sub2", "emptydir"})
        self.assertFalse(by_name["root.txt"].is_dir)
        self.assertEqual(by_name["root.txt"].size, len("root-content"))
        self.assertEqual(by_name["root.txt"].inner_path, "root.txt")
        self.assertIsNotNone(by_name["root.txt"].modified)
        for folder in ("sub", "sub2", "emptydir"):
            self.assertTrue(by_name[folder].is_dir)
            self.assertEqual(by_name[folder].size, 0)
        # フォルダが先頭に来る
        self.assertTrue(entries[0].is_dir)

    def test_list_implicit_folders(self):
        import zipfile

        archive = os.path.join(self.root, "implicit.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a/b/c.txt", "c")  # フォルダ項目なしで深いパスだけ入れる
        entries = file_ops.list_zip_entries(archive)
        self.assertEqual([(e.name, e.is_dir) for e in entries], [("a", True)])
        entries = file_ops.list_zip_entries(archive, "a")
        self.assertEqual([(e.name, e.is_dir) for e in entries], [("b", True)])
        entries = file_ops.list_zip_entries(archive, "a/b")
        self.assertEqual([(e.name, e.is_dir, e.inner_path) for e in entries], [("c.txt", False, "a/b/c.txt")])

    def test_list_subfolder(self):
        entries = file_ops.list_zip_entries(self._make_archive(), "sub")
        by_name = {e.name: e for e in entries}
        self.assertEqual(set(by_name), {"inner.txt", "deep"})
        self.assertEqual(by_name["inner.txt"].inner_path, "sub/inner.txt")
        self.assertTrue(by_name["deep"].is_dir)
        entries = file_ops.list_zip_entries(self._make_archive(), "sub/deep")
        self.assertEqual([e.name for e in entries], ["deeper.txt"])

    def test_list_missing_dir_returns_empty(self):
        self.assertEqual(file_ops.list_zip_entries(self._make_archive(), "nope"), [])

    def test_list_rejects_traversal(self):
        with self.assertRaises(ValueError):
            file_ops.list_zip_entries(self._make_archive(), "../outside")

    def test_extract_single_entry(self):
        archive = self._make_archive()
        out = os.path.join(self.root, "one")
        os.makedirs(out)
        path = file_ops.extract_zip_entry(archive, "sub/inner.txt", out)
        self.assertEqual(path, os.path.join(out, "sub", "inner.txt"))
        self.assertEqual(read(path), "inner-content")

    def test_extract_single_entry_default_temp(self):
        path = file_ops.extract_zip_entry(self._make_archive(), "root.txt")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(read(path), "root-content")

    def test_extract_missing_entry(self):
        with self.assertRaises(FileNotFoundError):
            file_ops.extract_zip_entry(self._make_archive(), "sub/nope.txt", self.dst)

    def test_extract_rejects_traversal(self):
        archive = self._make_archive()
        with self.assertRaises(ValueError):
            file_ops.extract_zip_entry(archive, "../escape.txt", self.dst)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape.txt")))


class HashTests(FileOpsTestCase):
    """v1.3.0: ハッシュ計算と照合。"""

    def _write_bytes(self, name, data: bytes):
        path = os.path.join(self.src, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_known_vectors_abc(self):
        path = self._write_bytes("abc.bin", b"abc")
        self.assertEqual(file_ops.compute_file_hash(path, "md5"), "900150983cd24fb0d6963f7d28e17f72")
        self.assertEqual(file_ops.compute_file_hash(path, "sha1"), "a9993e364706816aba3e25717850c26c9cd0d89d")
        self.assertEqual(
            file_ops.compute_file_hash(path, "sha256"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(
            file_ops.compute_file_hash(path, "sha512"),
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
            "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        )

    def test_known_vectors_empty(self):
        path = self._write_bytes("empty.bin", b"")
        self.assertEqual(file_ops.compute_file_hash(path, "md5"), "d41d8cd98f00b204e9800998ecf8427e")
        self.assertEqual(
            file_ops.compute_file_hash(path, "sha256"),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_default_algorithm_is_sha256(self):
        path = self._write_bytes("abc.bin", b"abc")
        self.assertEqual(file_ops.compute_file_hash(path), file_ops.compute_file_hash(path, "sha256"))

    def test_progress_reaches_total(self):
        path = self._write_bytes("data.bin", b"12345")
        seen = []
        file_ops.compute_file_hash(path, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[-1], (5, 5))

    def test_progress_for_empty_file(self):
        path = self._write_bytes("empty.bin", b"")
        seen = []
        file_ops.compute_file_hash(path, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen, [(0, 0)])

    def test_cancel_raises(self):
        path = self._write_bytes("data.bin", b"12345")
        with self.assertRaises(file_ops.OperationCancelled):
            file_ops.compute_file_hash(path, is_cancelled=lambda: True)

    def test_unknown_algorithm(self):
        path = self._write_bytes("data.bin", b"x")
        with self.assertRaises(ValueError):
            file_ops.compute_file_hash(path, "no-such-algorithm")

    def test_hash_matches_ignores_case_and_whitespace(self):
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertTrue(file_ops.hash_matches(digest, digest.upper()))
        self.assertTrue(file_ops.hash_matches(digest, "  " + digest + "\n"))
        self.assertTrue(file_ops.hash_matches(digest, digest[:32] + " " + digest[32:]))

    def test_hash_matches_rejects_wrong_and_empty(self):
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertFalse(file_ops.hash_matches(digest, "0" * 64))
        self.assertFalse(file_ops.hash_matches(digest, ""))
        self.assertFalse(file_ops.hash_matches(digest, "   \n"))


if __name__ == "__main__":
    unittest.main()


class ZipSecurityTests(FileOpsTestCase):
    """v1.3.0 セキュリティ修正: ZIP 展開のリンク経由脱出と ZIP 爆弾対策。"""

    def _make_zip(self, name, entries):
        import zipfile

        archive = os.path.join(self.root, name)
        with zipfile.ZipFile(archive, "w") as zf:
            for arcname, data in entries:
                zf.writestr(arcname, data)
        return archive

    def test_extract_does_not_follow_symlink_to_outside(self):
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        os.symlink(outside, os.path.join(out, "link"))
        archive = self._make_zip("link.zip", [("link/evil.txt", "bad"), ("ok.txt", "good")])
        created = file_ops.extract_zip(archive, out)
        self.assertFalse(os.path.exists(os.path.join(outside, "evil.txt")))
        self.assertEqual(created, [os.path.join(out, "ok.txt")])
        self.assertEqual(read(os.path.join(out, "ok.txt")), "good")

    def test_extract_into_destination_reached_via_symlink(self):
        real_dest = os.path.join(self.root, "real_out")
        os.makedirs(real_dest)
        link_dest = os.path.join(self.root, "link_out")
        os.symlink(real_dest, link_dest)
        write(os.path.join(self.src, "a.txt"), "hello")
        archive = file_ops.create_zip([os.path.join(self.src, "a.txt")], self.dst)
        file_ops.extract_zip(archive, link_dest)
        self.assertEqual(read(os.path.join(real_dest, "a.txt")), "hello")

    def test_extract_entry_does_not_follow_symlink_to_outside(self):
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        dest = os.path.join(self.root, "dest")
        os.makedirs(dest)
        os.symlink(outside, os.path.join(dest, "link"))
        archive = self._make_zip("link.zip", [("link/evil.txt", "bad")])
        with self.assertRaises(ValueError):
            file_ops.extract_zip_entry(archive, "link/evil.txt", dest)
        self.assertFalse(os.path.exists(os.path.join(outside, "evil.txt")))

    def test_extract_rejects_too_many_entries(self):
        archive = self._make_zip("many.zip", [("a.txt", "a"), ("b.txt", "b")])
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops, "MAX_ZIP_ENTRIES", 1):
            with self.assertRaises(ValueError):
                file_ops.extract_zip(archive, out)
        self.assertEqual(os.listdir(out), [])

    def test_extract_rejects_when_declared_total_exceeds_free_space(self):
        archive = self._make_zip("big.zip", [("a.txt", "x" * 100)])
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops.shutil, "disk_usage", return_value=mock.Mock(free=10)):
            with self.assertRaises(ValueError):
                file_ops.extract_zip(archive, out)
        self.assertEqual(os.listdir(out), [])

    def test_extract_rejects_entry_over_file_size_cap(self):
        archive = self._make_zip("cap.zip", [("a.txt", "x" * 100)])
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops, "MAX_ZIP_FILE_SIZE", 10):
            with self.assertRaises(ValueError) as ctx:
                file_ops.extract_zip(archive, out)
        self.assertIn("100 bytes > 上限 10 bytes", str(ctx.exception))
        self.assertEqual(os.listdir(out), [])

    def test_extract_rejects_total_over_total_size_cap(self):
        archive = self._make_zip(
            "totalcap.zip", [("a.txt", "x" * 60), ("b.txt", "y" * 60)]
        )
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops, "MAX_ZIP_TOTAL_SIZE", 100):
            with self.assertRaises(ValueError):
                file_ops.extract_zip(archive, out)
        self.assertEqual(os.listdir(out), [])

    def test_extract_entry_rejects_over_file_size_cap(self):
        archive = self._make_zip("entrycap.zip", [("a.txt", "x" * 100)])
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops, "MAX_ZIP_FILE_SIZE", 10):
            with self.assertRaises(ValueError):
                file_ops.extract_zip_entry(archive, "a.txt", out)
        self.assertEqual(os.listdir(out), [])

    def test_extract_within_size_caps_still_works(self):
        archive = self._make_zip("small.zip", [("a.txt", "hello")])
        out = os.path.join(self.root, "out")
        os.makedirs(out)
        with mock.patch.object(file_ops, "MAX_ZIP_FILE_SIZE", 100), \
                mock.patch.object(file_ops, "MAX_ZIP_TOTAL_SIZE", 100):
            created = file_ops.extract_zip(archive, out)
            self.assertEqual(created, [os.path.join(out, "a.txt")])
            self.assertEqual(read(os.path.join(out, "a.txt")), "hello")
            single = os.path.join(self.root, "single")
            os.makedirs(single)
            result = file_ops.extract_zip_entry(archive, "a.txt", single)
            self.assertEqual(read(result), "hello")

    def test_copy_zip_entry_guard_rejects_oversized_stream(self):
        import io

        with self.assertRaises(ValueError):
            file_ops._copy_zip_entry(io.BytesIO(b"x" * 10), io.BytesIO(), 5, None)
        out = io.BytesIO()
        written = file_ops._copy_zip_entry(io.BytesIO(b"12345"), out, 5, None)
        self.assertEqual(written, 5)
        self.assertEqual(out.getvalue(), b"12345")
