import base64
import os
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet  # noqa: E402

from core import file_utils  # noqa: E402

FAST = 1000  # テストを速くするための PBKDF2 反復回数
SMALL_CHUNK = 1024  # 複数チャンクにするための小さなチャンクサイズ


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class FileUtilsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        patcher = mock.patch.object(file_utils, "CHUNK_SIZE", SMALL_CHUNK)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def make_file(self, name, data):
        p = self.path(name)
        write_bytes(p, data)
        return p

    def encrypt(self, src, password="pw-for-test", **kwargs):
        return file_utils.encrypt_file(src, password, iterations=FAST, **kwargs)

    def leftover_temp_files(self):
        return [n for n in os.listdir(self.dir) if n.startswith(".ngf-")]


class RoundTripTests(FileUtilsTestCase):
    def roundtrip(self, data):
        src = self.make_file("a.bin", data)
        enc = self.encrypt(src)
        os.remove(src)
        out = file_utils.decrypt_file(enc, "pw-for-test")
        self.assertEqual(read_bytes(out), data)
        self.assertEqual(out, src)  # '.enc' を外した名前に戻る

    def test_small_file(self):
        self.roundtrip(b"hello world")

    def test_empty_file(self):
        self.roundtrip(b"")

    def test_exactly_one_chunk(self):
        self.roundtrip(os.urandom(SMALL_CHUNK))

    def test_exact_multiple_of_chunk(self):
        self.roundtrip(os.urandom(SMALL_CHUNK * 3))

    def test_multiple_chunks_with_remainder(self):
        self.roundtrip(os.urandom(SMALL_CHUNK * 5 + 123))

    def test_output_name_and_original_untouched(self):
        src = self.make_file("a.txt", b"data")
        enc = self.encrypt(src)
        self.assertEqual(enc, src + ".enc")
        self.assertEqual(read_bytes(src), b"data")
        self.assertTrue(file_utils.has_encrypted_header(enc))
        self.assertFalse(file_utils.has_encrypted_header(src))

    def test_ciphertext_is_not_plaintext_and_differs_each_time(self):
        src = self.make_file("a.txt", b"secret-secret-secret")
        enc1 = self.encrypt(src, output_path=self.path("1.enc"))
        enc2 = self.encrypt(src, output_path=self.path("2.enc"))
        self.assertNotIn(b"secret", read_bytes(enc1))
        self.assertNotEqual(read_bytes(enc1), read_bytes(enc2))  # salt / nonce が毎回違う

    def test_progress_reaches_total(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 3 + 5))
        seen = []
        enc = self.encrypt(src, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[-1][0], seen[-1][1])
        file_utils.decrypt_file(enc, "pw-for-test", output_path=self.path("out.bin"),
                                progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[-1][0], seen[-1][1])

    def test_unicode_password_is_normalized(self):
        src = self.make_file("a.txt", b"data")
        nfd = "\u304b\u3099" * 4  # 「が」を濁点を分けた形で入力
        nfc = "\u304c" * 4  # 「が」の1文字の形
        enc = self.encrypt(src, password=nfd)
        out = file_utils.decrypt_file(enc, nfc, output_path=self.path("out.txt"))
        self.assertEqual(read_bytes(out), b"data")


class FailureTests(FileUtilsTestCase):
    def test_wrong_password_raises_and_leaves_nothing(self):
        src = self.make_file("a.txt", os.urandom(SMALL_CHUNK * 2))
        enc = self.encrypt(src)
        os.remove(src)
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "wrong-password")
        self.assertEqual(sorted(os.listdir(self.dir)), ["a.txt.enc"])  # 出力も一時ファイルも残らない

    def test_tampered_body_is_detected(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 3))
        enc = self.encrypt(src)
        data = bytearray(read_bytes(enc))
        data[file_utils.HEADER_SIZE + 500] ^= 0x01
        write_bytes(enc, bytes(data))
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "pw-for-test")
        self.assertEqual(self.leftover_temp_files(), [])

    def test_tampered_header_is_detected(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 2))
        enc = self.encrypt(src)
        data = bytearray(read_bytes(enc))
        data[file_utils.HEADER_SIZE - 1] ^= 0x01  # nonce 接頭辞の最後の1バイト
        write_bytes(enc, bytes(data))
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "pw-for-test")

    def test_truncated_at_chunk_boundary_is_detected(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 3))
        enc = self.encrypt(src)
        data = read_bytes(enc)
        block = SMALL_CHUNK + file_utils.TAG_SIZE
        write_bytes(enc, data[: file_utils.HEADER_SIZE + block * 2])  # 最後のチャンクを丸ごと削る
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "pw-for-test")

    def test_appended_garbage_is_detected(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 2))
        enc = self.encrypt(src)
        write_bytes(enc, read_bytes(enc) + b"garbage-after-the-end")
        with self.assertRaises(file_utils.FileToolError):
            file_utils.decrypt_file(enc, "pw-for-test")

    def test_swapped_chunks_are_detected(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 3))
        enc = self.encrypt(src)
        data = read_bytes(enc)
        block = SMALL_CHUNK + file_utils.TAG_SIZE
        h = file_utils.HEADER_SIZE
        swapped = data[:h] + data[h + block: h + 2 * block] + data[h: h + block] + data[h + 2 * block:]
        write_bytes(enc, swapped)
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "pw-for-test")

    def test_hostile_iteration_count_is_rejected_quickly(self):
        header = struct.pack(file_utils.HEADER_FMT, file_utils.MAGIC, file_utils.VERSION,
                             4_000_000_000, SMALL_CHUNK, os.urandom(16), os.urandom(7))
        p = self.make_file("evil.enc", header + os.urandom(64))
        with self.assertRaises(file_utils.InvalidFileError):
            file_utils.decrypt_file(p, "pw")

    def test_too_short_files_are_rejected(self):
        p = self.make_file("short.enc", file_utils.MAGIC + b"\x01")
        with self.assertRaises(file_utils.InvalidFileError):
            file_utils.decrypt_file(p, "pw")
        p2 = self.make_file("tiny.bin", b"abc")
        with self.assertRaises(file_utils.InvalidFileError):
            file_utils.decrypt_file(p2, "pw")

    def test_random_file_is_not_decryptable(self):
        p = self.make_file("random.bin", os.urandom(300))
        with self.assertRaises(file_utils.FileToolError):
            file_utils.decrypt_file(p, "pw")

    def test_empty_password_is_rejected(self):
        src = self.make_file("a.txt", b"data")
        with self.assertRaises(ValueError):
            file_utils.encrypt_file(src, "", iterations=FAST)
        enc = self.encrypt(src)
        with self.assertRaises(ValueError):
            file_utils.decrypt_file(enc, "")

    def test_folder_and_missing_file_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            file_utils.encrypt_file(self.dir, "pw", iterations=FAST)
        with self.assertRaises(FileNotFoundError):
            file_utils.decrypt_file(self.path("nope.enc"), "pw")


class NoOverwriteTests(FileUtilsTestCase):
    def test_decrypt_does_not_overwrite_existing_original(self):
        src = self.make_file("a.txt", b"old content")
        enc = self.encrypt(src)
        write_bytes(src, b"edited after encryption")  # 暗号化後に元ファイルを編集した
        out = file_utils.decrypt_file(enc, "pw-for-test")
        self.assertEqual(read_bytes(src), b"edited after encryption")  # 元は無事
        self.assertEqual(out, self.path("a (2).txt"))
        self.assertEqual(read_bytes(out), b"old content")

    def test_encrypt_does_not_overwrite_existing_enc(self):
        src = self.make_file("a.txt", b"new")
        existing = self.make_file("a.txt.enc", b"important previous file")
        out = self.encrypt(src)
        self.assertEqual(read_bytes(existing), b"important previous file")
        self.assertEqual(out, self.path("a (2).txt.enc"))  # 元の拡張子 (.txt) を保つ

    def test_explicit_output_that_exists_is_an_error(self):
        src = self.make_file("a.txt", b"data")
        target = self.make_file("out.enc", b"keep me")
        with self.assertRaises(FileExistsError):
            self.encrypt(src, output_path=target)
        self.assertEqual(read_bytes(target), b"keep me")
        self.assertEqual(self.leftover_temp_files(), [])


class CancelAndVerifyTests(FileUtilsTestCase):
    def test_cancel_during_encrypt_leaves_no_files(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 5))
        calls = []

        def cancel_after_first():
            calls.append(1)
            return len(calls) > 1

        with self.assertRaises(file_utils.OperationCancelled):
            self.encrypt(src, is_cancelled=cancel_after_first)
        self.assertEqual(os.listdir(self.dir), ["a.bin"])

    def test_cancel_during_decrypt_leaves_no_files(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 5))
        enc = self.encrypt(src)
        os.remove(src)
        calls = []

        def cancel_after_first():
            calls.append(1)
            return len(calls) > 1

        with self.assertRaises(file_utils.OperationCancelled):
            file_utils.decrypt_file(enc, "pw-for-test", is_cancelled=cancel_after_first)
        self.assertEqual(os.listdir(self.dir), ["a.bin.enc"])

    def test_verify_ok_and_failures(self):
        src = self.make_file("a.bin", os.urandom(SMALL_CHUNK * 2 + 7))
        enc = self.encrypt(src)
        file_utils.verify_file(enc, "pw-for-test")  # 例外が出なければ OK
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.verify_file(enc, "wrong")
        with self.assertRaises(file_utils.InvalidFileError):
            file_utils.verify_file(src, "pw-for-test")  # 暗号化ファイルではない


class LegacyFormatTests(FileUtilsTestCase):
    @staticmethod
    def legacy_encrypt(path, password: bytes):
        """旧版 (Fernet) と同じ方法でファイルを暗号化する"""
        salt = os.urandom(16)
        key = base64.urlsafe_b64encode(file_utils._derive_key(password, salt, 100_000))
        return salt + Fernet(key).encrypt(read_bytes(path))

    def test_legacy_file_can_be_decrypted(self):
        src = self.make_file("old.txt", b"legacy content")
        enc = self.path("old.txt.enc")
        write_bytes(enc, self.legacy_encrypt(src, b"old-password"))
        os.remove(src)
        out = file_utils.decrypt_file(enc, "old-password")
        self.assertEqual(read_bytes(out), b"legacy content")

    def test_legacy_wrong_password(self):
        src = self.make_file("old.txt", b"legacy content")
        enc = self.path("old.txt.enc")
        write_bytes(enc, self.legacy_encrypt(src, b"old-password"))
        with self.assertRaises(file_utils.DecryptionError):
            file_utils.decrypt_file(enc, "not-it")
        self.assertEqual(self.leftover_temp_files(), [])

    def test_legacy_password_without_normalization(self):
        """旧版は NFC 正規化をしていなかったので、NFD のパスワードで作ったファイルも開ける"""
        nfd = "\u304b\u3099" * 4
        src = self.make_file("old.txt", b"legacy content")
        enc = self.path("old.txt.enc")
        write_bytes(enc, self.legacy_encrypt(src, nfd.encode("utf-8")))
        out = file_utils.decrypt_file(enc, nfd)
        self.assertEqual(read_bytes(out), b"legacy content")


class LooksEncryptedTests(FileUtilsTestCase):
    def test_detection(self):
        plain = self.make_file("a.txt", b"hello")
        enc = self.encrypt(plain)
        self.assertFalse(file_utils.looks_encrypted(plain))
        self.assertTrue(file_utils.looks_encrypted(enc))
        self.assertTrue(file_utils.looks_encrypted(self.path("whatever.ENC")))  # 拡張子だけでも


class ShredTests(FileUtilsTestCase):
    def test_file_is_overwritten_renamed_then_removed(self):
        original = os.urandom(5000)
        p = self.make_file("secret.bin", original)
        real_remove = os.remove
        captured = {}

        def spy_remove(target):
            captured["path"] = target
            captured["data"] = read_bytes(target)  # 削除される直前の中身
            real_remove(target)

        with mock.patch.object(file_utils.os, "remove", side_effect=spy_remove):
            file_utils.shred_file(p, passes=2)

        self.assertFalse(os.path.exists(p))
        self.assertEqual(os.listdir(self.dir), [])
        self.assertEqual(len(captured["data"]), len(original))  # 同じサイズが上書きされた
        self.assertNotEqual(captured["data"], original)
        self.assertNotEqual(os.path.basename(captured["path"]), "secret.bin")  # 名前も変わっていた

    def test_multi_chunk_and_progress(self):
        with mock.patch.object(file_utils, "SHRED_CHUNK_SIZE", 1000):
            p = self.make_file("big.bin", os.urandom(3500))
            seen = []
            file_utils.shred_file(p, passes=3, progress=lambda d, t: seen.append((d, t)))
        self.assertFalse(os.path.exists(p))
        self.assertEqual(seen[-1], (3500 * 3, 3500 * 3))

    def test_empty_file(self):
        p = self.make_file("empty.bin", b"")
        file_utils.shred_file(p)
        self.assertFalse(os.path.exists(p))

    def test_read_only_file(self):
        p = self.make_file("ro.bin", b"read only data")
        os.chmod(p, stat.S_IREAD)
        file_utils.shred_file(p)
        self.assertFalse(os.path.exists(p))

    def test_symlink_is_rejected_and_target_untouched(self):
        target = self.make_file("target.bin", b"precious")
        link = self.path("link.bin")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("この環境ではシンボリックリンクを作れません")
        with self.assertRaises(ValueError):
            file_utils.shred_file(link)
        self.assertEqual(read_bytes(target), b"precious")
        self.assertTrue(os.path.islink(link))

    def test_folder_and_missing_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            file_utils.shred_file(self.dir)
        with self.assertRaises(FileNotFoundError):
            file_utils.shred_file(self.path("nope.bin"))

    def test_invalid_passes(self):
        p = self.make_file("a.bin", b"x")
        with self.assertRaises(ValueError):
            file_utils.shred_file(p, passes=0)
        self.assertTrue(os.path.exists(p))

    def test_cancel_keeps_file_in_place(self):
        with mock.patch.object(file_utils, "SHRED_CHUNK_SIZE", 1000):
            p = self.make_file("a.bin", os.urandom(5000))
            calls = []

            def cancel_after_two():
                calls.append(1)
                return len(calls) > 2

            with self.assertRaises(file_utils.OperationCancelled):
                file_utils.shred_file(p, is_cancelled=cancel_after_two)
        self.assertTrue(os.path.exists(p))  # 削除されず、同じ名前のまま残る


if __name__ == "__main__":
    unittest.main()


class IterationLimitTests(FileUtilsTestCase):
    """v1.3.0: 暗号化側の反復回数にも上限を適用する (復号側の検証と一致)。"""

    def test_iterations_above_max_is_rejected_on_encrypt(self):
        src = self.make_file("a.bin", b"data")
        with self.assertRaises(ValueError):
            file_utils.encrypt_file(src, "pw", iterations=file_utils.MAX_ITERATIONS + 1)

    def test_iterations_below_min_is_rejected_on_encrypt(self):
        src = self.make_file("a.bin", b"data")
        with self.assertRaises(ValueError):
            file_utils.encrypt_file(src, "pw", iterations=file_utils.MIN_ITERATIONS - 1)

    def test_round_trip_still_works(self):
        src = self.make_file("a.bin", b"data")
        enc = self.encrypt(src)
        out = file_utils.decrypt_file(enc, "pw-for-test")
        self.assertEqual(read_bytes(out), b"data")
