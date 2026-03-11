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
import urllib.error
import urllib.request
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class LicenseStatus:
    status: str
    expires_at: datetime | None = None
    trial_days_left: int | None = None
    machine_id: str | None = None
    error: str | None = None


class LicenseManager:
    _REG_PATH = r"Software\Tradingm5UI\License"
    _REG_VALUE = "TrialAnchor"

    def __init__(
        self,
        product_name: str = "Tradingm5UI",
        trial_days: int = 7,
        trusted_time_provider: Callable[[], datetime | None] | None = None,
    ) -> None:
        self.product_name = product_name
        self.trial_days = trial_days
        self._trusted_time_provider = trusted_time_provider
        self.strict_trusted_time = _env_bool("LICENSE_STRICT_TRUSTED_TIME", True)
        self.require_manual_activation = _env_bool("LICENSE_REQUIRE_MANUAL_ACTIVATION", True)
        self.validation_url = os.getenv("LICENSE_VALIDATION_URL", "").strip()
        self.validation_token = os.getenv("LICENSE_VALIDATION_TOKEN", "").strip()
        self.require_online_validation = _env_bool("LICENSE_REQUIRE_ONLINE_VALIDATION", False)
        self.online_validation_timeout_sec = max(
            1.0,
            float(os.getenv("LICENSE_ONLINE_VALIDATION_TIMEOUT_SEC", "5") or "5"),
        )

        self.machine_id = self._machine_fingerprint()
        self.state_dir = self._resolve_state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.trial_state_path = self.state_dir / "trial_state.json"
        self.license_store_path = self.state_dir / "license.json"
        self.hidden_anchor_path = self.state_dir / ".trial_anchor.dat"
        self._secret = hashlib.sha256(f"{self.product_name}:{self.machine_id}".encode("utf-8")).digest()

    def build_license_request(
        self,
    ) -> dict[str, Any]:
        return {
            "product": self.product_name,
            "machine_hash": self.machine_id,
            "requested_at_utc": _iso(_utc_now()),
            "system": {
                "node": platform.node(),
                "os": platform.system(),
                "os_release": platform.release(),
                "machine": platform.machine(),
            },
        }

    def create_license_request_file(
        self,
        output_path: str = "license_request.json",
    ) -> dict[str, Any]:
        req = self.build_license_request()
        out = Path(output_path).resolve()
        out.write_text(json.dumps(req, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "file_path": str(out),
            "machine_hash": self.machine_id,
            "requested_at_utc": req["requested_at_utc"],
        }

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

    @staticmethod
    def _set_hidden(path: Path) -> None:
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass

    def _sign_json(self, payload: dict[str, Any], purpose: str) -> str:
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        key = hashlib.sha256(self._secret + purpose.encode("utf-8")).digest()
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    def _sign_state(self, payload: dict[str, Any]) -> str:
        return self._sign_json(payload, "state")

    def _sign_anchor(self, payload: dict[str, Any]) -> str:
        return self._sign_json(payload, "anchor")

    def _usage_chain_step(self, prev_chain: str, usage_counter: int, seen_utc: str, trusted_utc: str | None) -> str:
        payload = {
            "prev_chain": prev_chain,
            "usage_counter": usage_counter,
            "seen_utc": seen_utc,
            "trusted_utc": trusted_utc,
        }
        return self._sign_json(payload, "usage_chain")

    def _anchor_payload_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "anchor_id": state.get("anchor_id"),
            "machine_id": state.get("machine_id"),
            "first_run_utc": state.get("first_run_utc"),
        }

    def _write_registry_anchor(self, anchor_payload: dict[str, Any]) -> None:
        if winreg is None:
            return
        data = dict(anchor_payload)
        data["sig"] = self._sign_anchor(anchor_payload)
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._REG_PATH) as key:
                winreg.SetValueEx(key, self._REG_VALUE, 0, winreg.REG_SZ, raw)
        except Exception:
            # In strict mode, inability to persist anchor is treated later as invalid.
            pass

    def _read_registry_anchor(self) -> dict[str, Any] | None:
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._REG_PATH) as key:
                raw, _ = winreg.QueryValueEx(key, self._REG_VALUE)
            data = json.loads(str(raw))
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    def _write_hidden_anchor(self, anchor_payload: dict[str, Any]) -> None:
        digest = self._sign_json(anchor_payload, "hidden_anchor")
        self.hidden_anchor_path.write_text(digest, encoding="utf-8")
        self._set_hidden(self.hidden_anchor_path)

    def _read_hidden_anchor(self) -> str | None:
        try:
            if not self.hidden_anchor_path.exists():
                return None
            return self.hidden_anchor_path.read_text(encoding="utf-8").strip() or None
        except Exception:
            return None

    def _validate_anchor_integrity(self, state: dict[str, Any]) -> None:
        anchor_payload = self._anchor_payload_from_state(state)
        expected_anchor_sig = self._sign_anchor(anchor_payload)
        expected_hidden = self._sign_json(anchor_payload, "hidden_anchor")

        reg = self._read_registry_anchor()
        if reg is None:
            raise RuntimeError("Registry trial anchor missing")
        reg_payload = {
            "anchor_id": reg.get("anchor_id"),
            "machine_id": reg.get("machine_id"),
            "first_run_utc": reg.get("first_run_utc"),
        }
        reg_sig = str(reg.get("sig", ""))
        if reg_payload != anchor_payload or reg_sig != expected_anchor_sig:
            raise RuntimeError("Registry trial anchor mismatch")

        hidden = self._read_hidden_anchor()
        if hidden is None:
            raise RuntimeError("Hidden trial anchor missing")
        if hidden != expected_hidden:
            raise RuntimeError("Hidden trial anchor mismatch")

    def _persist_anchor_integrity(self, state: dict[str, Any]) -> None:
        anchor_payload = self._anchor_payload_from_state(state)
        self._write_registry_anchor(anchor_payload)
        self._write_hidden_anchor(anchor_payload)

    def _state_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "first_run_utc": state.get("first_run_utc"),
            "machine_id": state.get("machine_id"),
            "last_seen_utc": state.get("last_seen_utc"),
            "last_trusted_utc": state.get("last_trusted_utc"),
            "usage_counter": int(state.get("usage_counter", 0) or 0),
            "chain_head": state.get("chain_head"),
            "anchor_id": state.get("anchor_id"),
        }

    def _save_trial_state(self, state: dict[str, Any]) -> None:
        payload = self._state_payload(state)
        data = dict(payload)
        data["sig"] = self._sign_state(payload)
        self.trial_state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _ensure_trial_state(self) -> dict[str, Any]:
        if self.trial_state_path.exists():
            raw = json.loads(self.trial_state_path.read_text(encoding="utf-8"))
            sig = str(raw.get("sig", ""))
            payload = self._state_payload(raw)
            if sig == self._sign_state(payload):
                if not raw.get("last_seen_utc"):
                    raw["last_seen_utc"] = raw.get("first_run_utc")
                if "last_trusted_utc" not in raw:
                    raw["last_trusted_utc"] = None
                if "usage_counter" not in raw:
                    raw["usage_counter"] = 0
                if not raw.get("chain_head"):
                    raw["chain_head"] = self._sign_json(
                        {
                            "seed": f"{raw.get('machine_id')}|{raw.get('first_run_utc')}",
                        },
                        "usage_chain_seed",
                    )
                if not raw.get("anchor_id"):
                    raw["anchor_id"] = self._sign_json(
                        {
                            "machine_id": raw.get("machine_id"),
                            "first_run_utc": raw.get("first_run_utc"),
                        },
                        "anchor_id",
                    )[:24]
                self._validate_anchor_integrity(raw)
                return raw

        now_iso = _iso(_utc_now())
        anchor_id = self._sign_json(
            {
                "machine_id": self.machine_id,
                "first_run_utc": now_iso,
            },
            "anchor_id",
        )[:24]
        chain_seed = self._sign_json(
            {"seed": f"{self.machine_id}|{now_iso}"},
            "usage_chain_seed",
        )
        state = {
            "first_run_utc": now_iso,
            "machine_id": self.machine_id,
            "last_seen_utc": now_iso,
            "last_trusted_utc": None,
            "usage_counter": 0,
            "chain_head": chain_seed,
            "anchor_id": anchor_id,
        }
        self._save_trial_state(state)
        self._persist_anchor_integrity(state)
        self._validate_anchor_integrity(state)
        return state

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
        if self.strict_trusted_time and trusted_now is None:
            raise RuntimeError("Trusted market time unavailable (strict mode)")
        if trusted_now is None:
            return local_now, None
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
        payload_product = str(payload.get("product", self.product_name)).strip() or self.product_name
        if payload_product != self.product_name:
            return False, "License product mismatch"
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

    def _online_validate_license(
        self,
        doc: dict[str, Any],
        reference_now: datetime,
    ) -> tuple[bool, str | None]:
        """Optional server-side validation for high-value license checks."""
        if not self.validation_url:
            if self.require_online_validation:
                return False, "LICENSE_VALIDATION_URL is required in strict online validation mode"
            return True, None
        payload = doc.get("payload")
        if not isinstance(payload, dict):
            return False, "Invalid license payload for online validation"
        body = {
            "product": self.product_name,
            "machine_id": self.machine_id,
            "license_id": str(payload.get("license_id", "")),
            "machine_hash": str(payload.get("machine_hash", "")),
            "issued_at": str(payload.get("issued_at", "")),
            "expires_at": str(payload.get("expires_at", "")),
            "reference_now_utc": _iso(reference_now),
        }
        req = urllib.request.Request(
            self.validation_url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.validation_token}"} if self.validation_token else {}),
            },
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.online_validation_timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                out = json.loads(raw) if raw else {}
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if self.require_online_validation:
                return False, f"Online validation failed: {exc}"
            return True, None

        if not isinstance(out, dict):
            if self.require_online_validation:
                return False, "Online validation returned invalid response"
            return True, None
        allow = bool(out.get("allow", out.get("ok", True)))
        if not allow:
            return False, str(out.get("reason", "License rejected by validation service"))
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
            ref_now, _ = self._reference_now()
            doc = json.loads(file_path.read_text(encoding="utf-8"))
            ok, err = self._verify_license_document(doc, reference_now=ref_now)
            if not ok:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error=err,
                )
            ok, err = self._online_validate_license(doc, reference_now=ref_now)
            if not ok:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error=err,
                )
            self.license_store_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            return self._status_from_valid_license_doc(doc, ref_now)
        except Exception as exc:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=str(exc),
            )

    def status(self) -> LicenseStatus:
        try:
            ref_now, trusted_now = self._reference_now()
        except Exception as exc:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=str(exc),
            )

        if self.license_store_path.exists():
            try:
                doc = json.loads(self.license_store_path.read_text(encoding="utf-8"))
                ok, err = self._verify_license_document(doc, reference_now=ref_now)
                if ok:
                    ok, err = self._online_validate_license(doc, reference_now=ref_now)
                if ok:
                    return self._status_from_valid_license_doc(doc, ref_now)
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

        if self.require_manual_activation:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error="License not activated. Generate license_request.json and get signed license from vendor.",
            )

        try:
            trial = self._ensure_trial_state()
        except Exception as exc:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=str(exc),
            )

        if str(trial.get("machine_id")) != self.machine_id:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error="Trial state machine mismatch",
            )

        # Local clock rollback detection.
        last_seen_raw = str(trial.get("last_seen_utc") or trial.get("first_run_utc"))
        last_seen = _parse_iso(last_seen_raw)
        if _utc_now() + timedelta(minutes=5) < last_seen:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error="System clock rollback detected",
            )

        # Trusted-time rollback detection.
        if trusted_now is not None and trial.get("last_trusted_utc"):
            prev_trusted = _parse_iso(str(trial["last_trusted_utc"]))
            if trusted_now + timedelta(minutes=5) < prev_trusted:
                return LicenseStatus(
                    status="license_invalid",
                    machine_id=self.machine_id,
                    error="Trusted market time rollback detected",
                )

        # Monotonic signed usage chain.
        prev_chain = str(trial.get("chain_head") or "")
        usage_counter = int(trial.get("usage_counter", 0) or 0)
        seen_utc = _iso(_utc_now())
        trusted_utc = _iso(trusted_now) if trusted_now is not None else None
        next_counter = usage_counter + 1
        next_chain = self._usage_chain_step(prev_chain, next_counter, seen_utc, trusted_utc)

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

        trial["last_seen_utc"] = seen_utc
        trial["last_trusted_utc"] = trusted_utc if trusted_utc is not None else trial.get("last_trusted_utc")
        trial["usage_counter"] = next_counter
        trial["chain_head"] = next_chain
        try:
            self._save_trial_state(trial)
            # Ensure secondary anchors remain consistent.
            self._validate_anchor_integrity(trial)
        except Exception as exc:
            return LicenseStatus(
                status="license_invalid",
                machine_id=self.machine_id,
                error=str(exc),
            )

        days_left = max((expires - ref_now).days, 0)
        return LicenseStatus(
            status="trial_active",
            expires_at=expires,
            trial_days_left=days_left,
            machine_id=self.machine_id,
        )

    def _status_from_valid_license_doc(self, doc: dict[str, Any], ref_now: datetime) -> LicenseStatus:
        payload = doc.get("payload") if isinstance(doc, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        expires_at = _parse_iso(str(payload.get("expires_at")))
        license_type = str(payload.get("license_type", "paid")).strip().lower()
        trial_days_left: int | None = None
        if license_type == "trial":
            trial_days_left = max((expires_at - ref_now).days, 0)
        return LicenseStatus(
            status="license_valid",
            expires_at=expires_at,
            trial_days_left=trial_days_left,
            machine_id=self.machine_id,
        )
