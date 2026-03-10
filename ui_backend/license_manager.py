"""Offline trial and signed license verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import subprocess
import uuid
from typing import Any, Callable

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover - non-windows fallback
    winreg = None

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class LicenseStatus:
    status: str
    expires_at: datetime | None = None
    trial_days_left: int | None = None
    machine_id: str | None = None
    error: str | None = None


class LicenseManager:
    def __init__(
        self,
        product_name: str = "Tradingm5UI",
        trial_days: int = 7,
        trusted_time_provider: Callable[[], datetime | None] | None = None,
    ) -> None:
        self.product_name = product_name
        self.trial_days = trial_days
        self._trusted_time_provider = trusted_time_provider
        self.machine_id = self._machine_fingerprint()
        self.state_dir = self._resolve_state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.trial_state_path = self.state_dir / "trial_state.json"
        self.license_store_path = self.state_dir / "license.json"
        self._secret = hashlib.sha256(f"{self.product_name}:{self.machine_id}".encode("utf-8")).digest()

    def _resolve_state_dir(self) -> Path:
        base = os.getenv("PROGRAMDATA", "")
        if base:
            return Path(base) / self.product_name
        return Path(".") / ".license_state"

    def _machine_fingerprint(self) -> str:
        machine_guid = self._windows_machine_guid()
        hardware_uuid = self._hardware_uuid()
        volume_serial = self._system_volume_serial()
        parts = [
            platform.node(),
            platform.system(),
            platform.machine(),
            str(uuid.getnode()),
            machine_guid or "",
            hardware_uuid or "",
            volume_serial or "",
        ]
        raw = "|".join(parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _windows_machine_guid() -> str | None:
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value).strip() or None
        except Exception:
            return None

    @staticmethod
    def _hardware_uuid() -> str | None:
        # Best-effort hardware UUID (survives app reinstall and usually OS reinstall).
        try:
            out = subprocess.check_output(
                ["wmic", "csproduct", "get", "uuid"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            if len(lines) >= 2 and lines[0].lower() == "uuid":
                return lines[1]
        except Exception:
            pass
        return None

    @staticmethod
    def _system_volume_serial() -> str | None:
        try:
            serial = ctypes.c_uint32()
            max_comp_len = ctypes.c_uint32()
            file_sys_flags = ctypes.c_uint32()
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p("C:\\"),
                None,
                0,
                ctypes.byref(serial),
                ctypes.byref(max_comp_len),
                ctypes.byref(file_sys_flags),
                None,
                0,
            )
            if ok:
                return format(serial.value, "08X")
        except Exception:
            pass
        return None

    def _sign_state(self, payload: dict[str, Any]) -> str:
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        return sig

    def _ensure_trial_state(self) -> dict[str, Any]:
        if self.trial_state_path.exists():
            raw = json.loads(self.trial_state_path.read_text(encoding="utf-8"))
            sig = str(raw.get("sig", ""))
            payload = {
                "first_run_utc": raw.get("first_run_utc"),
                "machine_id": raw.get("machine_id"),
                "last_seen_utc": raw.get("last_seen_utc"),
                "last_trusted_utc": raw.get("last_trusted_utc"),
            }
            if sig == self._sign_state(payload):
                # Backward-compatible migration from old state format.
                if not raw.get("last_seen_utc"):
                    raw["last_seen_utc"] = raw.get("first_run_utc")
                if "last_trusted_utc" not in raw:
                    raw["last_trusted_utc"] = None
                return raw
        payload = {
            "first_run_utc": _iso(_utc_now()),
            "machine_id": self.machine_id,
            "last_seen_utc": _iso(_utc_now()),
            "last_trusted_utc": None,
        }
        data = dict(payload)
        data["sig"] = self._sign_state(payload)
        self.trial_state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _save_trial_state(self, state: dict[str, Any]) -> None:
        payload = {
            "first_run_utc": state.get("first_run_utc"),
            "machine_id": state.get("machine_id"),
            "last_seen_utc": state.get("last_seen_utc"),
            "last_trusted_utc": state.get("last_trusted_utc"),
        }
        data = dict(payload)
        data["sig"] = self._sign_state(payload)
        self.trial_state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _trusted_now(self) -> datetime | None:
        if self._trusted_time_provider is None:
            return None
        try:
            dt = self._trusted_time_provider()
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _reference_now(self) -> tuple[datetime, datetime | None]:
        local_now = _utc_now()
        trusted_now = self._trusted_now()
        if trusted_now is None:
            return local_now, None
        # Use the greater value to block system clock rollback extension.
        return max(local_now, trusted_now), trusted_now

    def _load_public_key(self) -> Ed25519PublicKey | None:
        key_b64 = os.getenv("LICENSE_PUBLIC_KEY_B64", "").strip()
        if not key_b64:
            return None
        key_bytes = base64.b64decode(key_b64.encode("utf-8"))
        return Ed25519PublicKey.from_public_bytes(key_bytes)

    def _verify_license_document(
        self,
        doc: dict[str, Any],
        reference_now: datetime | None = None,
    ) -> tuple[bool, str | None]:
        pub = self._load_public_key()
        if pub is None:
            return False, "Missing LICENSE_PUBLIC_KEY_B64"
        payload = doc.get("payload")
        signature_b64 = str(doc.get("signature", ""))
        if not isinstance(payload, dict) or not signature_b64:
            return False, "Invalid license format"
        if str(payload.get("machine_hash", "")) != self.machine_id:
            return False, "License is not valid for this machine"
        message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            signature = base64.b64decode(signature_b64.encode("utf-8"))
            pub.verify(signature, message)
        except Exception:
            return False, "Signature verification failed"
        expires_at = _parse_iso(str(payload.get("expires_at")))
        now_ref = reference_now or _utc_now()
        if now_ref > expires_at:
            return False, "License expired"
        return True, None

    def activate_from_file(self, path: str) -> LicenseStatus:
        file_path = Path(path)
        if not file_path.exists():
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=f"License file not found: {path}",
            )
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
            ref_now, _trusted_now = self._reference_now()
            ok, err = self._verify_license_document(doc, reference_now=ref_now)
            if not ok:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error=err,
                )
            self.license_store_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            expires_at = _parse_iso(str(doc["payload"]["expires_at"]))
            return LicenseStatus(
                status="license_valid",
                expires_at=expires_at,
                machine_id=self.machine_id,
            )
        except Exception as exc:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=str(exc),
            )

    def status(self) -> LicenseStatus:
        ref_now, trusted_now = self._reference_now()
        if self.license_store_path.exists():
            try:
                doc = json.loads(self.license_store_path.read_text(encoding="utf-8"))
                ok, err = self._verify_license_document(doc, reference_now=ref_now)
                if ok:
                    return LicenseStatus(
                        status="license_valid",
                        expires_at=_parse_iso(str(doc["payload"]["expires_at"])),
                        machine_id=self.machine_id,
                    )
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error=err,
                )
            except Exception as exc:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error=str(exc),
                )

        trial = self._ensure_trial_state()
        if str(trial.get("machine_id")) != self.machine_id:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error="Trial state machine mismatch",
            )
        # System time rollback detection.
        last_seen_raw = str(trial.get("last_seen_utc") or trial.get("first_run_utc"))
        last_seen = _parse_iso(last_seen_raw)
        if _utc_now() + timedelta(minutes=5) < last_seen:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error="System clock rollback detected",
            )
        if trusted_now is not None and trial.get("last_trusted_utc"):
            prev_trusted = _parse_iso(str(trial["last_trusted_utc"]))
            if trusted_now + timedelta(minutes=5) < prev_trusted:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error="Trusted market time rollback detected",
                )
        first_run = _parse_iso(str(trial["first_run_utc"]))
        expires = first_run + timedelta(days=self.trial_days)
        if ref_now > expires:
            return LicenseStatus(
                status="trial_expired",
                expires_at=expires,
                trial_days_left=0,
                machine_id=self.machine_id,
                error="Trial expired. Activate a license file.",
            )
        trial["last_seen_utc"] = _iso(_utc_now())
        trial["last_trusted_utc"] = _iso(trusted_now) if trusted_now is not None else trial.get("last_trusted_utc")
        self._save_trial_state(trial)
        days_left = max((expires - ref_now).days, 0)
        return LicenseStatus(
            status="trial_active",
            expires_at=expires,
            trial_days_left=days_left,
            machine_id=self.machine_id,
        )
