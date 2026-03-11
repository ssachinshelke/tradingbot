"""Print machine hash used by local license validation."""

from __future__ import annotations

import argparse

from ui_backend.license_manager import LicenseManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Print machine hash or generate license request file")
    parser.add_argument("--request-out", default="", help="Optional path to write license_request.json")
    args = parser.parse_args()

    mgr = LicenseManager()
    if args.request_out:
        out = mgr.create_license_request_file(output_path=args.request_out)
        print(f"Request file written: {out['file_path']}")
        print(f"Machine hash: {out['machine_hash']}")
        return
    print(mgr.machine_id)


if __name__ == "__main__":
    main()
