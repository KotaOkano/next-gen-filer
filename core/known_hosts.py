from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile

STATUS_KNOWN = "known"  # 保存済みの鍵と一致した
STATUS_UNKNOWN = "unknown"  # このホストの鍵はまだ保存されていない（初めての接続）
STATUS_CHANGED = "changed"  # 保存済みの鍵と違う（サーバーの入れ替え、または中間者攻撃の可能性）


def host_label(host: str, port: int) -> str:
    host = host.strip().lower()
    return host if port == 22 else f"[{host}]:{port}"


def fingerprint(key_blob: bytes) -> str:
    """OpenSSH と同じ形式のフィンガープリント（SHA256:xxxx...）"""
    digest = hashlib.sha256(key_blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class KnownHosts:
    def __init__(self, path: str):
        self.path = path

    def _load(self) -> list:
        """[(ホスト表記, 鍵の種類, 鍵のバイト列), ...]。壊れた行は無視する。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []

        entries = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                blob = base64.b64decode(parts[2], validate=True)
            except (ValueError, binascii.Error):
                continue
            entries.append((parts[0], parts[1], blob))
        return entries

    def _save(self, entries: list) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".kh-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                for label, key_type, blob in entries:
                    f.write(f"{label} {key_type} {base64.b64encode(blob).decode('ascii')}\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ---- 参照 --------------------------------------------------------
    def lookup(self, host: str, port: int) -> list:
        """このホストの保存済みの鍵 [(鍵の種類, 鍵のバイト列), ...]"""
        label = host_label(host, port)
        return [(t, b) for h, t, b in self._load() if h == label]

    def known_types(self, host: str, port: int) -> list:
        return [t for t, _ in self.lookup(host, port)]

    def status(self, host: str, port: int, key_type: str, key_blob: bytes) -> str:
        stored = self.lookup(host, port)
        if not stored:
            return STATUS_UNKNOWN
        if any(t == key_type and b == key_blob for t, b in stored):
            return STATUS_KNOWN
        return STATUS_CHANGED

    # ---- 更新 --------------------------------------------------------
    def add(self, host: str, port: int, key_type: str, key_blob: bytes) -> None:
        """鍵を信頼済みとして保存する。同じホスト・同じ種類の古い鍵は置き換える。"""
        if not key_type or any(c.isspace() for c in key_type):
            raise ValueError("鍵の種類が不正です")
        label = host_label(host, port)
        entries = [e for e in self._load() if not (e[0] == label and e[1] == key_type)]
        entries.append((label, key_type, key_blob))
        self._save(entries)

    def remove(self, host: str, port: int) -> None:
        label = host_label(host, port)
        entries = self._load()
        remaining = [e for e in entries if e[0] != label]
        if len(remaining) != len(entries):
            self._save(remaining)
