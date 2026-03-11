"""Vendor utility: generate Ed25519 key pair for offline license signing."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Ed25519 keypair for Tradingm5 licenses")
    parser.add_argument("--output-dir", default=".", help="Directory to write key files")
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    private_b64 = base64.b64encode(private_raw).decode("utf-8")
    public_b64 = base64.b64encode(public_raw).decode("utf-8")

    private_path = out_dir / "vendor_private_key.b64.txt"
    public_path = out_dir / "vendor_public_key.b64.txt"
    private_path.write_text(private_b64, encoding="utf-8")
    public_path.write_text(public_b64, encoding="utf-8")

    print(f"Private key written: {private_path}")
    print(f"Public key written:  {public_path}")
    print("Set LICENSE_PUBLIC_KEY_B64 on client builds using vendor_public_key.b64.txt")


if __name__ == "__main__":
    main()
