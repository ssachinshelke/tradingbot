"""Offline trial and signed license verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import uuid
from typing import Any

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
    def __init__(self, product_name: str = "Tradingm5UI", trial_days: int = 7) -> None:
        self.product_name = product_name
        self.trial_days = trial_days
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
        parts = [
            platform.node(),
            platform.system(),
            platform.machine(),
            str(uuid.getnode()),
        ]
        raw = "|".join(parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

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
            }
            if sig == self._sign_state(payload):
                return raw
        payload = {
            "first_run_utc": _iso(_utc_now()),
            "machine_id": self.machine_id,
        }
        data = dict(payload)
        data["sig"] = self._sign_state(payload)
        self.trial_state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _load_public_key(self) -> Ed25519PublicKey | None:
        key_b64 = os.getenv("LICENSE_PUBLIC_KEY_B64", "").strip()
        if not key_b64:
            return None
        key_bytes = base64.b64decode(key_b64.encode("utf-8"))
        return Ed25519PublicKey.from_public_bytes(key_bytes)

    def _verify_license_document(self, doc: dict[str, Any]) -> tuple[bool, str | None]:
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
        if _utc_now() > expires_at:
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
            ok, err = self._verify_license_document(doc)
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
        if self.license_store_path.exists():
            try:
                doc = json.loads(self.license_store_path.read_text(encoding="utf-8"))
                ok, err = self._verify_license_document(doc)
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
        first_run = _parse_iso(str(trial["first_run_utc"]))
        expires = first_run + timedelta(days=self.trial_days)
        if _utc_now() > expires:
            return LicenseStatus(
                status="trial_expired",
                expires_at=expires,
                trial_days_left=0,
                machine_id=self.machine_id,
                error="Trial expired. Activate a license file.",
            )
        days_left = max((expires - _utc_now()).days, 0)
        return LicenseStatus(
            status="trial_active",
            expires_at=expires,
            trial_days_left=days_left,
            machine_id=self.machine_id,
        )
