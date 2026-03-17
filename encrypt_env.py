"""
encrypt_env.py - Encrypts env_local.txt and env_vps.txt using Fernet (AES-128-CBC).
Creates .enc versions alongside the originals.
Usage:
    python encrypt_env.py          -> encrypts with prompted password
    python encrypt_env.py --decrypt -> decrypts .enc files back (for recovery)
"""

import os
import sys
import base64
import hashlib
import getpass
from cryptography.fernet import Fernet

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILES = ["env_local.txt", "env_vps.txt"]


def derive_key(password: str) -> bytes:
    """Derive a Fernet-compatible key from a password using SHA-256."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_files(password: str):
    key = derive_key(password)
    f = Fernet(key)
    encrypted_count = 0
    for fname in ENV_FILES:
        fpath = os.path.join(SCRIPT_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  [SKIP] {fname} not found")
            continue
        with open(fpath, "rb") as fh:
            data = fh.read()
        encrypted = f.encrypt(data)
        enc_path = fpath + ".enc"
        with open(enc_path, "wb") as fh:
            fh.write(encrypted)
        encrypted_count += 1
        print(f"  [OK] {fname} -> {fname}.enc ({len(encrypted)} bytes)")
    return encrypted_count


def decrypt_files(password: str):
    key = derive_key(password)
    f = Fernet(key)
    for fname in ENV_FILES:
        enc_path = os.path.join(SCRIPT_DIR, fname + ".enc")
        if not os.path.exists(enc_path):
            print(f"  [SKIP] {fname}.enc not found")
            continue
        with open(enc_path, "rb") as fh:
            encrypted = fh.read()
        try:
            decrypted = f.decrypt(encrypted)
        except Exception:
            print(f"  [ERROR] {fname}.enc - wrong password or corrupted file")
            continue
        out_path = os.path.join(SCRIPT_DIR, fname + ".recovered")
        with open(out_path, "wb") as fh:
            fh.write(decrypted)
        print(f"  [OK] {fname}.enc -> {fname}.recovered")


def main():
    mode = "encrypt"
    if len(sys.argv) > 1 and sys.argv[1] == "--decrypt":
        mode = "decrypt"

    print(f"\n{'='*50}")
    print(f"  BuySell365 - {'Encrypt' if mode == 'encrypt' else 'Decrypt'} Credentials")
    print(f"{'='*50}\n")

    password = getpass.getpass("Enter password: ")
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)

    if mode == "encrypt":
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
        count = encrypt_files(password)
        if count > 0:
            print(f"\n  {count} file(s) encrypted successfully.")
            print("  The original .txt files were NOT modified.")
            print("  You can now optionally delete the .txt originals")
            print("  and keep only the .enc files for security.\n")
    else:
        decrypt_files(password)

    print()


if __name__ == "__main__":
    main()
