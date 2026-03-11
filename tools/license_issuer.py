"""Issuer utility: generate and sign offline license files.

This script is for vendor/internal use only.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _load_private_key_b64(cli_value: str) -> str:
    if cli_value.strip():
        return cli_value.strip()
    env_value = os.getenv("LICENSE_PRIVATE_KEY_B64", "").strip()
    if env_value:
        return env_value
    key_file = Path("vendor_private_key.b64.txt")
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign offline Tradingm5 license")
    parser.add_argument(
        "--private-key-b64",
        default="",
        help="Ed25519 private key bytes in base64 (or set LICENSE_PRIVATE_KEY_B64 env var)",
    )
    parser.add_argument("--machine-hash", default="")
    parser.add_argument("--request-file", default="license_request.json", help="Path to customer license_request.json")
    parser.add_argument("--days", type=int, default=0, help="If omitted/0, defaults by license type")
    parser.add_argument("--edition", default=os.getenv("LICENSE_DEFAULT_EDITION", "pro"))
    parser.add_argument(
        "--license-type",
        choices=["trial", "paid"],
        default=os.getenv("LICENSE_DEFAULT_TYPE", "trial").strip().lower() or "trial",
    )
    parser.add_argument("--product", default=os.getenv("LICENSE_DEFAULT_PRODUCT", "Tradingm5UI"))
    parser.add_argument("--output", default=os.getenv("LICENSE_DEFAULT_OUTPUT", "license.json"))
    args = parser.parse_args()

    private_key_b64 = _load_private_key_b64(args.private_key_b64)
    if not private_key_b64:
        raise SystemExit(
            "private key is required (use --private-key-b64, or LICENSE_PRIVATE_KEY_B64, "
            "or local vendor_private_key.b64.txt)"
        )
    private_key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(private_key_b64.encode("utf-8"))
    )
    issued_at = datetime.now(timezone.utc)
    default_days = 7 if args.license_type == "trial" else 365
    days = max(1, args.days if args.days > 0 else int(os.getenv("LICENSE_DEFAULT_DAYS", str(default_days))))
    expires_at = issued_at + timedelta(days=days)
    machine_hash = args.machine_hash
    if args.request_file:
        req_doc = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        machine_hash = str(req_doc.get("machine_hash", machine_hash)).strip()
    if not machine_hash:
        raise SystemExit("machine hash is required (pass --machine-hash or --request-file)")
    payload = {
        "product": args.product,
        "machine_hash": machine_hash,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "edition": args.edition,
        "license_type": args.license_type,
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
