"""Шифрование норки: каталог лежит в публичном репозитории только в зашифрованном виде.

    python crypto_store.py encrypt data/norka.json norka/norka.enc
    python crypto_store.py decrypt norka/norka.enc data/norka.json
    python crypto_store.py genkey
Ключ — переменная окружения STASH_DATA_KEY.
"""
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet


def _f() -> Fernet:
    key = os.getenv("STASH_DATA_KEY", "").strip()
    if not key:
        sys.exit("STASH_DATA_KEY не задан")
    return Fernet(key.encode())


def encrypt_bytes(data: bytes) -> bytes:
    return _f().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return _f().decrypt(data)


def encrypt_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(encrypt_bytes(Path(src).read_bytes()))


def decrypt_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(decrypt_bytes(Path(src).read_bytes()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "genkey":
        print(Fernet.generate_key().decode())
    elif cmd in ("encrypt", "decrypt") and len(sys.argv) == 4:
        (encrypt_file if cmd == "encrypt" else decrypt_file)(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        sys.exit(__doc__)
