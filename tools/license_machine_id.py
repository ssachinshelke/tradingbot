"""Print machine hash used by local license validation."""

from __future__ import annotations

from ui_backend.license_manager import LicenseManager


def main() -> None:
    mgr = LicenseManager()
    print(mgr.machine_id)


if __name__ == "__main__":
    main()
