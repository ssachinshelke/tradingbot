"""Issuer utility: generate and sign offline license files.

This script is for vendor/internal use only.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign offline Tradingm5 license")
    parser.add_argument("--private-key-b64", required=True, help="Ed25519 private key bytes in base64")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--machine-hash", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--edition", default="pro")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    private_key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(args.private_key_b64.encode("utf-8"))
    )
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(days=max(1, args.days))
    payload = {
        "customer_id": args.customer_id,
        "machine_hash": args.machine_hash,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "edition": args.edition,
    }
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(message)
    document = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("utf-8"),
        "algo": "ed25519",
    }
    out = Path(args.output)
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"Wrote license to {out}")


if __name__ == "__main__":
    main()
