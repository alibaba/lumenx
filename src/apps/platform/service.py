"""Business services for the creator platform.

The service owns platform tables only. Core/Studio artifacts are accepted as
immutable snapshots supplied by a future read-only resolver.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import sqlite3
import uuid
from typing import Any
from math import ceil
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import PlatformConfig
from .contracts import TaskStatus, can_transition
from .db import connect, migrate


class PlatformError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message); self.code = code; self.message = message; self.status = status


def now() -> str: return datetime.now(timezone.utc).isoformat()
def uid() -> str: return str(uuid.uuid4())


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return digest.hex(), salt.hex()


def verify_password(password: str, digest_hex: str, salt_hex: str) -> bool:
    digest, _ = hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(digest, digest_hex)


class PlatformService:
    def __init__(self, config: PlatformConfig | None = None):
        self.config = config or PlatformConfig.from_env()
        self.path = self.config.sqlite_path()
        migrate(self.path)

    def _idem(self, c: sqlite3.Connection, actor: str, key: str) -> dict[str, Any] | None:
        row = c.execute("SELECT response_json FROM idempotency_records WHERE actor_id=? AND key=?", (actor, key)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_idem(self, c: sqlite3.Connection, actor: str, key: str, response: dict[str, Any]) -> None:
        c.execute("INSERT INTO idempotency_records VALUES (?,?,?,?)", (actor, key, json.dumps(response), now()))

    def _audit(self, c: sqlite3.Connection, actor: str | None, event: str, entity: str, entity_id: str | None, payload: dict[str, Any]) -> None:
        c.execute("INSERT INTO audit_events_ext VALUES (?,?,?,?,?,?,?,?)", (uid(), actor, uid(), event, entity, entity_id, json.dumps(payload), now()))

    def register(self, username: str, password: str, email: str | None = None, role: str = "user") -> dict[str, Any]:
        if len(password) < self.config.password_min_length: raise PlatformError("weak_password", "password is too short")
        if role not in {"user", "creator", "reviewer", "client", "admin"}: raise PlatformError("invalid_role", "unsupported role")
        digest, salt = hash_password(password); user_id = uid(); timestamp = now()
        try:
            with connect(self.path) as c:
                c.execute("INSERT INTO platform_users VALUES (?,?,?,?,?,?,?,?,?,?,?)", (user_id, username, email, None, digest, salt, username, role, 1, timestamp, timestamp))
                if role == "creator": c.execute("INSERT INTO creator_certifications VALUES (?,?,?,?,?,?,?,?,?,?)", (uid(), user_id, "unapplied", "standard", "direct_exam", None, None, None, None, timestamp))
                self._audit(c, user_id, "user.registered", "user", user_id, {"role": role})
        except sqlite3.IntegrityError as exc: raise PlatformError("user_exists", "username or email already exists", 409) from exc
        return {"id": user_id, "username": username, "email": email, "role": role}

    def login(self, username: str, password: str) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT id,password_hash,password_salt,role,is_active FROM platform_users WHERE username=?", (username,)).fetchone()
            if not row or not row[4] or not verify_password(password, row[1], row[2]): raise PlatformError("invalid_credentials", "invalid credentials", 401)
            raw = secrets.token_urlsafe(32); token_hash = hashlib.sha256(raw.encode()).hexdigest(); sid = uid()
            expires = (datetime.now(timezone.utc) + timedelta(seconds=self.config.session_ttl_seconds)).isoformat()
            c.execute("INSERT INTO platform_sessions VALUES (?,?,?,?,?,?)", (sid, row[0], token_hash, expires, None, now()))
            return {"access_token": raw, "token_type": "bearer", "expires_at": expires, "user": {"id": row[0], "username": username, "role": row[3]}}

    def authenticate(self, token: str) -> dict[str, Any]:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with connect(self.path) as c:
            row = c.execute("SELECT u.id,u.username,u.role,s.id,s.expires_at FROM platform_sessions s JOIN platform_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL", (token_hash,)).fetchone()
            if not row or datetime.fromisoformat(row[4]) <= datetime.now(timezone.utc): raise PlatformError("session_invalid", "session is invalid or expired", 401)
            return {"id": row[0], "username": row[1], "role": row[2], "session_id": row[3]}

    def logout(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with connect(self.path) as c: c.execute("UPDATE platform_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (now(), token_hash))

    def _credentials_cipher(self) -> AESGCM:
        raw = self.config.credentials_key
        if not raw:
            raise PlatformError("credentials_key_required", "platform credential encryption is not configured", 503)
        try:
            key = base64.urlsafe_b64decode(raw.encode() + b"=" * (-len(raw) % 4))
        except Exception as exc:
            raise PlatformError("credentials_key_invalid", "platform credential encryption key is invalid", 500) from exc
        if len(key) != 32: raise PlatformError("credentials_key_invalid", "platform credential encryption key must decode to 32 bytes", 500)
        return AESGCM(key)

    @staticmethod
    def _credential_view(row) -> dict[str, Any]:
        return {"id": row[0], "provider": row[1], "label": row[2], "masked_key": f"{row[4][:4]}…{row[4][-4:]}", "status": row[5], "created_at": row[6], "updated_at": row[7], "last_verified_at": row[8], "revoked_at": row[9]}

    def list_credentials(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute("SELECT id,provider,label,ciphertext_b64,key_fingerprint,status,created_at,updated_at,last_verified_at,revoked_at FROM platform_credentials WHERE user_id=? ORDER BY created_at DESC", (actor["id"],)).fetchall()
        return [self._credential_view(row) for row in rows]

    def create_credential(self, actor: dict[str, Any], provider: str, label: str, api_key: str) -> dict[str, Any]:
        if not api_key or len(api_key) < 16: raise PlatformError("invalid_credential", "API-Key is too short")
        if not provider or len(provider) > 50: raise PlatformError("invalid_provider", "provider is invalid")
        if not label or len(label) > 80: raise PlatformError("invalid_label", "credential label is invalid")
        cipher = self._credentials_cipher(); nonce = secrets.token_bytes(12); credential_id = uid(); timestamp = now()
        ciphertext = cipher.encrypt(nonce, api_key.encode(), f"{actor['id']}:{provider}".encode())
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
        with connect(self.path) as c:
            c.execute("INSERT INTO platform_credentials VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (credential_id, actor["id"], provider, label, base64.urlsafe_b64encode(ciphertext).decode(), base64.urlsafe_b64encode(nonce).decode(), fingerprint, "active", timestamp, timestamp, None, None))
            self._audit(c, actor["id"], "credential.created", "credential", credential_id, {"provider": provider, "fingerprint": fingerprint[:12]})
            row = c.execute("SELECT id,provider,label,ciphertext_b64,key_fingerprint,status,created_at,updated_at,last_verified_at,revoked_at FROM platform_credentials WHERE id=?", (credential_id,)).fetchone()
        return self._credential_view(row)

    def verify_credential(self, actor: dict[str, Any], credential_id: str) -> dict[str, Any]:
        cipher = self._credentials_cipher()
        with connect(self.path) as c:
            row = c.execute("SELECT id,provider,label,ciphertext_b64,nonce_b64,key_fingerprint,status,created_at,updated_at,last_verified_at,revoked_at FROM platform_credentials WHERE id=? AND user_id=?", (credential_id, actor["id"])).fetchone()
            if not row: raise PlatformError("credential_not_found", "credential not found", 404)
            if row[6] != "active": raise PlatformError("credential_revoked", "credential is revoked", 409)
            try: cipher.decrypt(base64.urlsafe_b64decode(row[4]), base64.urlsafe_b64decode(row[3]), f"{actor['id']}:{row[1]}".encode())
            except Exception as exc: raise PlatformError("credential_corrupt", "credential cannot be decrypted", 500) from exc
            timestamp = now(); c.execute("UPDATE platform_credentials SET last_verified_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, credential_id))
            view = c.execute("SELECT id,provider,label,ciphertext_b64,key_fingerprint,status,created_at,updated_at,last_verified_at,revoked_at FROM platform_credentials WHERE id=?", (credential_id,)).fetchone()
        return self._credential_view(view)

    def rotate_credential(self, actor: dict[str, Any], credential_id: str, api_key: str) -> dict[str, Any]:
        if not api_key or len(api_key) < 16: raise PlatformError("invalid_credential", "API-Key is too short")
        cipher = self._credentials_cipher(); nonce = secrets.token_bytes(12)
        with connect(self.path) as c:
            row = c.execute("SELECT provider FROM platform_credentials WHERE id=? AND user_id=?", (credential_id, actor["id"])).fetchone()
            if not row: raise PlatformError("credential_not_found", "credential not found", 404)
            ciphertext = cipher.encrypt(nonce, api_key.encode(), f"{actor['id']}:{row[0]}".encode()); fingerprint = hashlib.sha256(api_key.encode()).hexdigest(); timestamp = now()
            c.execute("UPDATE platform_credentials SET ciphertext_b64=?,nonce_b64=?,key_fingerprint=?,status='active',updated_at=?,last_verified_at=NULL,revoked_at=NULL WHERE id=?", (base64.urlsafe_b64encode(ciphertext).decode(), base64.urlsafe_b64encode(nonce).decode(), fingerprint, timestamp, credential_id))
            view = c.execute("SELECT id,provider,label,ciphertext_b64,key_fingerprint,status,created_at,updated_at,last_verified_at,revoked_at FROM platform_credentials WHERE id=?", (credential_id,)).fetchone()
            self._audit(c, actor["id"], "credential.rotated", "credential", credential_id, {"provider": row[0], "fingerprint": fingerprint[:12]})
        return self._credential_view(view)

    def revoke_credential(self, actor: dict[str, Any], credential_id: str) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT provider FROM platform_credentials WHERE id=? AND user_id=?", (credential_id, actor["id"])).fetchone()
            if not row: raise PlatformError("credential_not_found", "credential not found", 404)
            timestamp = now(); c.execute("UPDATE platform_credentials SET status='revoked',revoked_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, credential_id)); self._audit(c, actor["id"], "credential.revoked", "credential", credential_id, {"provider": row[0]})
        return {"id": credential_id, "status": "revoked"}

    def resolve_credential(self, actor: dict[str, Any], credential_id: str) -> dict[str, Any]:
        cipher = self._credentials_cipher()
        with connect(self.path) as c:
            row = c.execute("SELECT provider,ciphertext_b64,nonce_b64,status FROM platform_credentials WHERE id=? AND user_id=?", (credential_id, actor["id"])).fetchone()
        if not row: raise PlatformError("credential_not_found", "credential not found", 404)
        if row[3] != "active": raise PlatformError("credential_revoked", "credential is revoked", 409)
        try: secret = cipher.decrypt(base64.urlsafe_b64decode(row[2]), base64.urlsafe_b64decode(row[1]), f"{actor['id']}:{row[0]}".encode()).decode()
        except Exception as exc: raise PlatformError("credential_corrupt", "credential cannot be decrypted", 500) from exc
        return {"credential_ref": credential_id, "provider": row[0], "secret": secret, "billing_mode": "byok"}

    def record_token_usage(self, actor: dict[str, Any], event: dict[str, Any]) -> None:
        prompt = max(0, int(event.get("prompt_tokens") or 0))
        completion = max(0, int(event.get("completion_tokens") or 0))
        total = max(0, int(event.get("total_tokens") or prompt + completion))
        product = event.get("product") if event.get("product") in {"studio", "atelier", "core"} else "core"
        with connect(self.path) as c:
            c.execute(
                "INSERT INTO platform_token_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid(), actor["id"], product, str(event.get("operation") or "llm.chat"),
                 str(event.get("provider") or "unknown"), event.get("requested_model"),
                 event.get("provider_model"), prompt, completion, total, event.get("request_id"),
                 json.dumps(event.get("metadata") or {}, ensure_ascii=False), now()),
            )

    def token_usage(self, actor: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        with connect(self.path) as c:
            totals = c.execute(
                "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), COALESCE(SUM(total_tokens),0) FROM platform_token_usage WHERE user_id=?",
                (actor["id"],),
            ).fetchone()
            rows = c.execute(
                "SELECT id,product,operation,provider,requested_model,provider_model,prompt_tokens,completion_tokens,total_tokens,request_id,created_at FROM platform_token_usage WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (actor["id"], limit),
            ).fetchall()
        keys = ("id", "product", "operation", "provider", "requested_model", "provider_model", "prompt_tokens", "completion_tokens", "total_tokens", "request_id", "created_at")
        return {"prompt_tokens": totals[0], "completion_tokens": totals[1], "total_tokens": totals[2], "records": [dict(zip(keys, row)) for row in rows]}

    def credit_pricing(self) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute("SELECT provider,model,pricing_version,input_credits_per_1k,output_credits_per_1k FROM credit_pricing WHERE active=1 ORDER BY provider,model").fetchall()
        return [dict(zip(("provider", "model", "pricing_version", "input_credits_per_1k", "output_credits_per_1k"), row)) for row in rows]

    def media_pricing(self) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute(
                "SELECT provider,model,media_kind,pricing_version,credits_per_unit FROM media_pricing WHERE active=1 ORDER BY provider,media_kind,model"
            ).fetchall()
        keys = ("provider", "model", "media_kind", "pricing_version", "credits_per_unit")
        return [dict(zip(keys, row)) for row in rows]

    @staticmethod
    def _skill_manifest(manifest: dict[str, Any]) -> tuple[str, str, str, str]:
        if not isinstance(manifest, dict):
            raise PlatformError("invalid_skill_manifest", "Skill manifest must be an object")
        forbidden = {"path", "source", "script", "command", "credential_ref", "provider_handle"}
        if forbidden.intersection(manifest):
            raise PlatformError("invalid_skill_manifest", "Skill manifest cannot contain executable or secret fields")
        name, version, content_hash = manifest.get("name"), manifest.get("version"), manifest.get("content_hash")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise PlatformError("invalid_skill_manifest", "Skill name is invalid")
        if not isinstance(version, str) or not version or len(version) > 32:
            raise PlatformError("invalid_skill_manifest", "Skill version is invalid")
        canonical = json.dumps({k: manifest[k] for k in sorted(manifest) if k != "content_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if content_hash != expected:
            raise PlatformError("invalid_skill_manifest", "Skill content hash does not match manifest")
        return name, version, content_hash, canonical

    def create_skill_version(self, actor: dict[str, Any], manifest: dict[str, Any], *, scope: str = "user", project_id: str | None = None, parent_version: str | None = None) -> dict[str, Any]:
        if scope not in {"system", "workspace", "user", "project"}:
            raise PlatformError("invalid_skill_scope", "unsupported Skill scope")
        if scope == "system" and actor.get("role") != "admin":
            raise PlatformError("skill_admin_required", "system Skill versions require admin", 403)
        if scope == "project" and not project_id:
            raise PlatformError("skill_project_required", "project Skill versions require project_id")
        if scope != "project" and project_id is not None:
            raise PlatformError("skill_project_forbidden", "project_id is only valid for project scope")
        name, version, content_hash, canonical = self._skill_manifest(manifest)
        owner_id = actor.get("id") if scope in {"user", "project"} else None
        skill_id = uid(); timestamp = now()
        try:
            with connect(self.path) as c:
                c.execute(
                    "INSERT INTO platform_skill_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (skill_id, name, version, scope, owner_id, project_id, parent_version, canonical, content_hash, "draft", timestamp),
                )
                self._audit(c, actor.get("id"), "skill.version.created", "skill_version", skill_id, {"name": name, "version": version, "scope": scope, "project_id": project_id})
        except sqlite3.IntegrityError as exc:
            raise PlatformError("skill_version_exists", "Skill version already exists", 409) from exc
        return {"id": skill_id, "name": name, "version": version, "scope": scope, "owner_id": owner_id, "project_id": project_id, "parent_version": parent_version, "content_hash": content_hash, "status": "draft", "created_at": timestamp}

    def bind_skill_version(self, actor: dict[str, Any], skill_version_id: str, *, active: bool = True) -> dict[str, Any]:
        timestamp = now()
        with connect(self.path) as c:
            row = c.execute("SELECT id,name,scope,owner_id,project_id,status FROM platform_skill_versions WHERE id=?", (skill_version_id,)).fetchone()
            if not row: raise PlatformError("skill_version_not_found", "Skill version not found", 404)
            if row[2] == "system" and actor.get("role") != "admin": raise PlatformError("skill_admin_required", "system Skill versions require admin", 403)
            if row[2] in {"user", "project"} and row[3] != actor.get("id"): raise PlatformError("skill_forbidden", "Skill version belongs to another user", 403)
            c.execute("UPDATE platform_skill_versions SET status=? WHERE id=?", ("active" if active else "draft", skill_version_id))
            c.execute("DELETE FROM platform_skill_bindings WHERE name=? AND scope=? AND owner_id IS ? AND project_id IS ?", (row[1], row[2], row[3], row[4]))
            if active:
                c.execute("INSERT INTO platform_skill_bindings VALUES (?,?,?,?,?,?,?,?,?)", (uid(), row[1], row[2], row[3], row[4], skill_version_id, 1, timestamp, timestamp))
            self._audit(c, actor.get("id"), "skill.version.bound", "skill_version", skill_version_id, {"active": active, "scope": row[2], "project_id": row[4]})
        return {"id": skill_version_id, "name": row[1], "scope": row[2], "project_id": row[4], "active": active}

    def resolve_skill_version(self, actor: dict[str, Any], name: str, *, project_id: str | None = None) -> dict[str, Any] | None:
        order = [("project", actor.get("id"), project_id), ("user", actor.get("id"), None), ("workspace", None, None), ("system", None, None)]
        with connect(self.path) as c:
            for scope, owner_id, scoped_project in order:
                row = c.execute("SELECT v.id,v.name,v.version,v.scope,v.owner_id,v.project_id,v.parent_version,v.manifest_json,v.content_hash FROM platform_skill_bindings b JOIN platform_skill_versions v ON v.id=b.skill_version_id WHERE b.name=? AND b.scope=? AND b.owner_id IS ? AND b.project_id IS ? AND b.active=1 AND v.status='active'", (name, scope, owner_id, scoped_project)).fetchone()
                if row:
                    keys = ("id", "name", "version", "scope", "owner_id", "project_id", "parent_version", "manifest_json", "content_hash")
                    return dict(zip(keys, row))
        return None

    def list_skill_versions(self, actor: dict[str, Any], *, name: str | None = None, project_id: str | None = None) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute(
                "SELECT id,name,version,scope,owner_id,project_id,parent_version,content_hash,status,created_at FROM platform_skill_versions WHERE (scope='system' OR (scope='user' AND owner_id=?) OR (scope='project' AND owner_id=? AND project_id=?)) AND (? IS NULL OR name=?) ORDER BY name,created_at DESC",
                (actor.get("id"), actor.get("id"), project_id, name, name),
            ).fetchall()
        keys = ("id", "name", "version", "scope", "owner_id", "project_id", "parent_version", "content_hash", "status", "created_at")
        return [dict(zip(keys, row)) for row in rows]

    def estimate_media_credits(self, provider: str, model: str, media_kind: str, units: int = 1) -> dict[str, Any]:
        if media_kind not in {"image", "video", "audio"} or type(units) is not int or units <= 0:
            raise PlatformError("invalid_media_usage", "media kind and units are invalid")
        with connect(self.path) as c:
            row = c.execute(
                "SELECT pricing_version,credits_per_unit FROM media_pricing WHERE provider=? AND model IN (?, '*') AND media_kind=? AND active=1 ORDER BY model DESC,created_at DESC LIMIT 1",
                (provider, model, media_kind),
            ).fetchone()
        if not row:
            raise PlatformError("pricing_unavailable", "no active media pricing for this model", 422)
        return {"provider": provider, "model": model, "media_kind": media_kind, "units": units,
                "pricing_version": row[0], "credits_per_unit": row[1], "estimated_credits": row[1] * units}

    def estimate_credits(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
        if min(prompt_tokens, completion_tokens) < 0:
            raise PlatformError("invalid_token_usage", "token counts cannot be negative")
        with connect(self.path) as c:
            row = c.execute("SELECT pricing_version,input_credits_per_1k,output_credits_per_1k FROM credit_pricing WHERE provider=? AND model=? AND active=1 ORDER BY created_at DESC LIMIT 1", (provider, model)).fetchone()
        if not row: raise PlatformError("pricing_unavailable", "no active credit pricing for this model", 422)
        input_credits = ceil(prompt_tokens * row[1] / 1000)
        output_credits = ceil(completion_tokens * row[2] / 1000)
        return {"provider": provider, "model": model, "pricing_version": row[0], "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "estimated_credits": input_credits + output_credits}

    def credit_balance(self, actor: dict[str, Any]) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT COALESCE(SUM(CASE WHEN direction IN ('credit','release','refund') THEN amount_credits ELSE -amount_credits END),0), COALESCE(SUM(CASE WHEN direction='credit' THEN amount_credits ELSE 0 END),0), COALESCE(SUM(CASE WHEN direction='debit' THEN amount_credits ELSE 0 END),0) FROM credit_ledger WHERE user_id=?", (actor["id"],)).fetchone()
        return {"user_id": actor["id"], "available_credits": row[0], "credited_credits": row[1], "debited_credits": row[2]}

    def credit_ledger(self, actor: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with connect(self.path) as c:
            rows = c.execute("SELECT id,amount_credits,direction,reference_type,reference_id,metadata_json,created_at FROM credit_ledger WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (actor["id"], limit)).fetchall()
        keys = ("id", "amount_credits", "direction", "reference_type", "reference_id", "metadata_json", "created_at")
        return [dict(zip(keys, row)) for row in rows]

    def grant_credits(self, issuer: dict[str, Any], user_id: str, amount: int, reason: str, idempotency_key: str) -> dict[str, Any]:
        if issuer["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "credit grants require admin or client", 403)
        if amount <= 0: raise PlatformError("invalid_credit_amount", "credit amount must be positive")
        with connect(self.path) as c:
            if not c.execute("SELECT 1 FROM platform_users WHERE id=? AND is_active=1", (user_id,)).fetchone(): raise PlatformError("user_not_found", "credit recipient not found", 404)
            existing = c.execute("SELECT id,amount_credits FROM credit_ledger WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing: return {"id": existing[0], "user_id": user_id, "amount_credits": existing[1], "status": "credited", "idempotent_replay": True}
            entry_id = uid()
            c.execute("INSERT INTO credit_ledger VALUES (?,?,?,?,?,?,?,?,?)", (entry_id, user_id, amount, "credit", "grant", user_id, idempotency_key, json.dumps({"reason": reason}, ensure_ascii=False), now()))
            self._audit(c, issuer["id"], "credits.granted", "user", user_id, {"amount_credits": amount, "reason": reason})
        return {"id": entry_id, "user_id": user_id, "amount_credits": amount, "status": "credited", "idempotent_replay": False}

    def reserve_credits(self, actor: dict[str, Any], estimated_credits: int, generation_id: str, pricing_version: str, idempotency_key: str) -> dict[str, Any]:
        if estimated_credits <= 0: raise PlatformError("invalid_credit_amount", "reservation must be positive")
        with connect(self.path) as c:
            old = c.execute("SELECT id,estimated_credits,status FROM credit_reservations WHERE idempotency_key=? AND user_id=?", (idempotency_key, actor["id"])).fetchone()
            if old: return {"reservation_id": old[0], "estimated_credits": old[1], "status": old[2], "idempotent_replay": True}
            balance = c.execute("SELECT COALESCE(SUM(CASE WHEN direction IN ('credit','release','refund') THEN amount_credits ELSE -amount_credits END),0) FROM credit_ledger WHERE user_id=?", (actor["id"],)).fetchone()[0]
            if balance < estimated_credits: raise PlatformError("insufficient_credits", "insufficient platform credits", 402)
            reservation_id = uid(); timestamp = now()
            c.execute("INSERT INTO credit_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?)", (reservation_id, actor["id"], generation_id, estimated_credits, 0, 0, pricing_version, "reserved", idempotency_key, timestamp, timestamp))
            c.execute("INSERT INTO credit_ledger VALUES (?,?,?,?,?,?,?,?,?)", (uid(), actor["id"], estimated_credits, "debit", "reservation", reservation_id, f"{idempotency_key}:reserve", "{}", timestamp))
        return {"reservation_id": reservation_id, "estimated_credits": estimated_credits, "status": "reserved", "idempotent_replay": False}

    def reserve_for_usage(self, actor: dict[str, Any], provider: str, model: str, prompt_tokens: int, completion_tokens: int, generation_id: str, idempotency_key: str) -> dict[str, Any]:
        estimate = self.estimate_credits(provider, model, prompt_tokens, completion_tokens)
        return {**self.reserve_credits(actor, estimate["estimated_credits"], generation_id, estimate["pricing_version"], idempotency_key), "pricing_version": estimate["pricing_version"], "estimated_credits": estimate["estimated_credits"]}

    def settle_for_usage(self, actor: dict[str, Any], reservation_id: str, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
        estimate = self.estimate_credits(provider, model, prompt_tokens, completion_tokens)
        return self.settle_credits(actor, reservation_id, estimate["estimated_credits"])

    def settle_credits(self, actor: dict[str, Any], reservation_id: str, actual_credits: int) -> dict[str, Any]:
        if actual_credits < 0: raise PlatformError("invalid_credit_amount", "actual credits cannot be negative")
        with connect(self.path) as c:
            row = c.execute("SELECT user_id,estimated_credits,status FROM credit_reservations WHERE id=?", (reservation_id,)).fetchone()
            if not row or row[0] != actor["id"]: raise PlatformError("reservation_not_found", "credit reservation not found", 404)
            if row[2] != "reserved": return {"reservation_id": reservation_id, "status": row[2], "actual_credits": actual_credits, "idempotent_replay": True}
            extra = max(0, actual_credits - row[1]); release = max(0, row[1] - actual_credits)
            if extra:
                balance = c.execute("SELECT COALESCE(SUM(CASE WHEN direction IN ('credit','release','refund') THEN amount_credits ELSE -amount_credits END),0) FROM credit_ledger WHERE user_id=?", (actor["id"],)).fetchone()[0]
                if balance < extra: raise PlatformError("insufficient_credits", "additional settlement credits are unavailable", 402)
                c.execute("INSERT INTO credit_ledger VALUES (?,?,?,?,?,?,?,?,?)", (uid(), actor["id"], extra, "debit", "settlement", reservation_id, f"{reservation_id}:extra", "{}", now()))
            if release:
                c.execute("INSERT INTO credit_ledger VALUES (?,?,?,?,?,?,?,?,?)", (uid(), actor["id"], release, "release", "settlement", reservation_id, f"{reservation_id}:release", "{}", now()))
            c.execute("UPDATE credit_reservations SET settled_credits=?,released_credits=?,status='settled',updated_at=? WHERE id=?", (actual_credits, release, now(), reservation_id))
        return {"reservation_id": reservation_id, "status": "settled", "actual_credits": actual_credits, "released_credits": release, "additional_credits": extra, "idempotent_replay": False}

    def release_credits(self, actor: dict[str, Any], reservation_id: str) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT user_id,estimated_credits,status FROM credit_reservations WHERE id=?", (reservation_id,)).fetchone()
            if not row or row[0] != actor["id"]: raise PlatformError("reservation_not_found", "credit reservation not found", 404)
            if row[2] != "reserved": return {"reservation_id": reservation_id, "status": row[2], "idempotent_replay": True}
            c.execute("INSERT INTO credit_ledger VALUES (?,?,?,?,?,?,?,?,?)", (uid(), actor["id"], row[1], "release", "reservation", reservation_id, f"{reservation_id}:release", "{}", now()))
            c.execute("UPDATE credit_reservations SET released_credits=?,status='released',updated_at=? WHERE id=?", (row[1], now(), reservation_id))
        return {"reservation_id": reservation_id, "status": "released", "released_credits": row[1], "idempotent_replay": False}

    def create_competition(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "only client or admin can publish competitions", 403)
        cid = uid(); timestamp = now()
        with connect(self.path) as c:
            c.execute("INSERT INTO competitions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cid, payload["title"], payload["slug"], payload.get("description", ""), payload.get("rules_markdown", ""), int(payload.get("whitelist_required", True)), int(payload.get("allocated_credits", 0)), payload.get("status", "draft"), payload.get("enroll_deadline"), payload.get("submit_deadline"), timestamp))
        return {"id": cid, **payload}

    def list_competitions(self) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute("SELECT id,title,slug,description,rules_markdown,whitelist_required,allocated_credits,status,enroll_deadline,submit_deadline,created_at FROM competitions ORDER BY created_at DESC").fetchall()
        keys = ["id","title","slug","description","rules_markdown","whitelist_required","allocated_credits","status","enroll_deadline","submit_deadline","created_at"]
        return [dict(zip(keys, row)) for row in rows]

    def competition(self, competition_id: str) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT id,title,slug,description,rules_markdown,whitelist_required,allocated_credits,status,enroll_deadline,submit_deadline,created_at FROM competitions WHERE id=?", (competition_id,)).fetchone()
        if not row: raise PlatformError("not_found", "competition not found", 404)
        return dict(zip(("id","title","slug","description","rules_markdown","whitelist_required","allocated_credits","status","enroll_deadline","submit_deadline","created_at"), row))

    def leaderboard(self, competition_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute("SELECT id,user_id,title,score,award_title,status FROM platform_submissions WHERE target_type='competition' AND target_id=? AND status='approved' ORDER BY score DESC, created_at ASC", (competition_id,)).fetchall()
        return [dict(zip(("submission_id","user_id","title","score","award_title","status"), row)) for row in rows]

    def enroll(self, actor: dict[str, Any], competition_id: str, invite_code: str | None, idem: str) -> dict[str, Any]:
        with connect(self.path) as c:
            old = self._idem(c, actor["id"], idem)
            if old: return old
            competition = c.execute("SELECT status,whitelist_required FROM competitions WHERE id=?", (competition_id,)).fetchone()
            if not competition: raise PlatformError("not_found", "competition not found", 404)
            if competition[0] not in {"enrolling", "ongoing"}: raise PlatformError("competition_closed", "competition is not open")
            if competition[1]:
                allowed = c.execute("SELECT 1 FROM competition_whitelists WHERE competition_id=? AND user_id=? AND status='approved' AND (invite_code IS NULL OR invite_code=?)", (competition_id, actor["id"], invite_code)).fetchone()
                if not allowed: raise PlatformError("not_whitelisted", "user is not on the competition whitelist", 403)
            result = {"competition_id": competition_id, "user_id": actor["id"], "status": "enrolled"}
            self._save_idem(c, actor["id"], idem, result); self._audit(c, actor["id"], "competition.enrolled", "competition", competition_id, result); return result

    def whitelist(self, actor: dict[str, Any], competition_id: str, user_id: str, invite_code: str | None = None) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "reviewer cannot manage whitelist", 403)
        item = {"id": uid(), "competition_id": competition_id, "user_id": user_id, "invite_code": invite_code, "status": "approved", "created_at": now()}
        with connect(self.path) as c:
            try: c.execute("INSERT INTO competition_whitelists VALUES (?,?,?,?,?,?)", tuple(item.values()))
            except sqlite3.IntegrityError as exc: raise PlatformError("already_whitelisted", "user already whitelisted", 409) from exc
        return item

    def submit_artifact(self, actor: dict[str, Any], target_type: str, target_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if target_type not in {"competition", "certification_exam"}: raise PlatformError("invalid_target", "invalid submission target")
        if target_type == "competition":
            with connect(self.path) as c:
                competition = c.execute("SELECT status,submit_deadline FROM competitions WHERE id=?", (target_id,)).fetchone()
            if not competition: raise PlatformError("not_found", "competition not found", 404)
            if competition[0] not in {"ongoing", "judging"}: raise PlatformError("competition_closed", "competition is not accepting submissions")
            if competition[1] and datetime.fromisoformat(competition[1]) < datetime.now(timezone.utc): raise PlatformError("submission_closed", "submission deadline has passed")
        sid = uid(); item = {"id": sid, "target_type": target_type, "target_id": target_id, "user_id": actor["id"], **{k: payload[k] for k in ("artifact_id","artifact_version","sha256","media_type","project_id","project_title_snapshot","title")}, "description": payload.get("description", ""), "status": "submitted", "created_at": now()}
        with connect(self.path) as c:
            c.execute("INSERT INTO platform_submissions (id,target_type,target_id,user_id,artifact_id,artifact_version,sha256,media_type,project_id,project_title_snapshot,title,description,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(item.values()))
        return item

    def review_submission(self, actor: dict[str, Any], submission_id: str, approved: bool, score: float | None, award_title: str | None = None) -> dict[str, Any]:
        if actor["role"] not in {"admin", "reviewer", "client"}: raise PlatformError("forbidden", "review role required", 403)
        status = "approved" if approved else "rejected"
        with connect(self.path) as c:
            row = c.execute("SELECT user_id,target_type,target_id FROM platform_submissions WHERE id=?", (submission_id,)).fetchone()
            if not row: raise PlatformError("not_found", "submission not found", 404)
            c.execute("UPDATE platform_submissions SET status=?,score=?,award_title=? WHERE id=?", (status,score,award_title,submission_id))
            if approved and row[1] == "competition" and award_title:
                c.execute("INSERT OR IGNORE INTO creator_certifications (id,user_id,status,level,source_type,submission_id,score,feedback,certified_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (uid(),row[0],"certified","standard","competition_award",submission_id,score,"competition award",now(),now()))
                c.execute("UPDATE creator_certifications SET status='certified',source_type='competition_award',submission_id=?,score=?,certified_at=? WHERE user_id=?", (submission_id,score,now(),row[0]))
            return {"submission_id": submission_id, "status": status, "score": score, "award_title": award_title}

    def create_task(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "only client or admin can publish tasks", 403)
        item = {"id": uid(), **payload, "status": payload.get("status", "draft"), "version": 0, "created_at": now()}
        with connect(self.path) as c: c.execute("INSERT INTO commercial_tasks (id,title,category,brief_markdown,bounty_cents,required_level,deadline,status,version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (item["id"],item["title"],item["category"],item["brief_markdown"],item["bounty_cents"],item.get("required_level","standard"),item["deadline"],item["status"],0,item["created_at"]))
        return item

    def task_square(self) -> list[dict[str, Any]]:
        with connect(self.path) as c: rows = c.execute("SELECT id,title,category,brief_markdown,bounty_cents,required_level,deadline,status,version,created_at FROM commercial_tasks WHERE status='published' ORDER BY deadline").fetchall()
        keys = ["id","title","category","brief_markdown","bounty_cents","required_level","deadline","status","version","created_at"]
        return [dict(zip(keys, r)) for r in rows]

    def claim_task(self, actor: dict[str, Any], task_id: str, idem: str) -> dict[str, Any]:
        if actor["role"] != "creator": raise PlatformError("forbidden", "only certified creators can claim tasks", 403)
        with connect(self.path) as c:
            old = self._idem(c, actor["id"], idem)
            if old: return old
            cert = c.execute("SELECT status FROM creator_certifications WHERE user_id=?", (actor["id"],)).fetchone()
            if not cert or cert[0] != "certified": raise PlatformError("creator_not_certified", "creator certification is required", 403)
            cur = c.execute("UPDATE commercial_tasks SET status='claimed',claimed_by=?,claim_time=?,version=version+1 WHERE id=? AND status='published'", (actor["id"], now(), task_id))
            if cur.rowcount != 1: raise PlatformError("task_unavailable", "task is no longer available", 409)
            result = {"task_id": task_id, "claimed_by": actor["id"], "status": "claimed"}; self._save_idem(c, actor["id"], idem, result); c.execute("INSERT INTO task_history VALUES (?,?,?,?,?,?,?,?)", (uid(),task_id,"published","claimed",actor["id"],None,idem,now())); return result

    def transition_task(self, actor: dict[str, Any], task_id: str, target: TaskStatus, reason: str | None = None) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT status,claimed_by,version FROM commercial_tasks WHERE id=?", (task_id,)).fetchone()
            if not row: raise PlatformError("not_found", "task not found", 404)
            current = TaskStatus(row[0]);
            if not can_transition(current, target): raise PlatformError("invalid_transition", f"cannot move {current} to {target}")
            if actor["role"] not in {"admin", "client", "reviewer"} and actor["id"] != row[1]: raise PlatformError("forbidden", "not task owner", 403)
            c.execute("UPDATE commercial_tasks SET status=?,version=version+1 WHERE id=? AND version=?", (target.value, task_id, row[2])); c.execute("INSERT INTO task_history VALUES (?,?,?,?,?,?,?,?)", (uid(),task_id,current.value,target.value,actor["id"],reason,uid(),now()))
            return {"task_id": task_id, "from_status": current.value, "status": target.value}

    def start_task(self, actor: dict[str, Any], task_id: str) -> dict[str, Any]:
        return self.transition_task(actor, task_id, TaskStatus.IN_PROGRESS, "creator started work")

    def deliver_task(self, actor: dict[str, Any], task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with connect(self.path) as c:
            row = c.execute("SELECT status,claimed_by FROM commercial_tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row[1] != actor["id"]: raise PlatformError("forbidden", "task is not assigned to this creator", 403)
            current = TaskStatus(row[0]); target = TaskStatus.DELIVERED if current in {TaskStatus.CLAIMED,TaskStatus.IN_PROGRESS} else TaskStatus.RESUBMITTED
            if not can_transition(current, target): raise PlatformError("invalid_transition", "task cannot accept delivery")
            n = c.execute("SELECT COALESCE(MAX(version_num),0)+1 FROM task_deliveries WHERE task_id=?", (task_id,)).fetchone()[0]; did=uid(); timestamp=now()
            c.execute("INSERT INTO task_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (did,task_id,actor["id"],payload["artifact_id"],payload["artifact_version"],payload["sha256"],payload["media_type"],payload["project_id"],payload["project_title_snapshot"],n,payload.get("delivery_note",""),"pending","",timestamp))
            c.execute("UPDATE commercial_tasks SET status=?,version=version+1 WHERE id=?", (target.value,task_id)); return {"id":did,"task_id":task_id,"version_num":n,"status":target.value}

    def review_task(self, actor: dict[str, Any], task_id: str, approved: bool, feedback: str = "") -> dict[str, Any]:
        if actor["role"] not in {"admin","client","reviewer"}: raise PlatformError("forbidden", "review role required", 403)
        with connect(self.path) as c:
            row=c.execute("SELECT status,claimed_by,bounty_cents FROM commercial_tasks WHERE id=?",(task_id,)).fetchone()
            if not row: raise PlatformError("not_found", "task not found",404)
            current=TaskStatus(row[0]); target=TaskStatus.ACCEPTED if approved else TaskStatus.REVISION_REQUESTED
            if not can_transition(current,target): raise PlatformError("invalid_transition", "task is not awaiting review")
            c.execute("UPDATE commercial_tasks SET status=?,version=version+1 WHERE id=?",(target.value,task_id)); c.execute("UPDATE task_deliveries SET review_status=?,review_feedback=? WHERE task_id=? AND review_status='pending'", ("approved" if approved else "revision_required",feedback,task_id))
            if approved and row[2] > 0:
                account = c.execute("SELECT id FROM ledger_accounts WHERE user_id=?",(row[1],)).fetchone(); account_id=account[0] if account else uid()
                if not account: c.execute("INSERT INTO ledger_accounts VALUES (?,?,?)",(account_id,row[1],now()))
                c.execute("INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?,?,?)",(uid(),uid(),account_id,row[2],"credit","task",task_id,"task-settlement-"+task_id,now()))
                c.execute("UPDATE commercial_tasks SET status='settled',version=version+1 WHERE id=?",(task_id,)); target=TaskStatus.SETTLED
            return {"task_id":task_id,"status":target.value,"feedback":feedback}

    def wallet(self, actor: dict[str, Any]) -> dict[str, Any]:
        with connect(self.path) as c:
            rows=c.execute("SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount_cents ELSE -amount_cents END),0) FROM ledger_entries le JOIN ledger_accounts la ON la.id=le.account_id WHERE la.user_id=?",(actor["id"],)).fetchone()
        return {"user_id":actor["id"],"simulated_balance_cents":rows[0]}

    def transactions(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with connect(self.path) as c:
            rows = c.execute("SELECT le.id,le.transaction_id,le.amount_cents,le.direction,le.reference_type,le.reference_id,le.created_at FROM ledger_entries le JOIN ledger_accounts la ON la.id=le.account_id WHERE la.user_id=? ORDER BY le.created_at DESC", (actor["id"],)).fetchall()
        return [dict(zip(("entry_id","transaction_id","amount_cents","direction","reference_type","reference_id","created_at"), row)) for row in rows]

    def release_expired_tasks(self) -> int:
        released = 0
        with connect(self.path) as c:
            rows = c.execute("SELECT id,status FROM commercial_tasks WHERE status IN ('published','claimed','in_progress') AND deadline < ?", (now(),)).fetchall()
            for task_id, status in rows:
                c.execute("UPDATE commercial_tasks SET status='expired',version=version+1 WHERE id=? AND status=?", (task_id,status))
                c.execute("INSERT INTO task_history VALUES (?,?,?,?,?,?,?,?)", (uid(),task_id,status,"expired","system","deadline",uid(),now())); released += 1
        return released

    def add_course(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "course publishing requires admin or client", 403)
        item = {"id": uid(), "title": payload["title"], "description": payload.get("description", ""), "video_url": payload.get("video_url", ""), "status": "published"}
        with connect(self.path) as c: c.execute("INSERT INTO courses VALUES (?,?,?,?,?)", tuple(item.values()))
        return item

    def list_courses(self) -> list[dict[str, Any]]:
        with connect(self.path) as c: rows = c.execute("SELECT id,title,description,video_url,status FROM courses WHERE status='published' ORDER BY title").fetchall()
        return [dict(zip(("id","title","description","video_url","status"), row)) for row in rows]

    def add_template(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "template publishing requires admin or client", 403)
        item = {"id": uid(), "title": payload["title"], "source_project_id": payload["source_project_id"], "version": payload.get("version", "1"), "metadata_json": json.dumps(payload.get("metadata", {})), "status": "published"}
        with connect(self.path) as c: c.execute("INSERT INTO templates VALUES (?,?,?,?,?,?)", tuple(item.values()))
        return item

    def list_templates(self) -> list[dict[str, Any]]:
        with connect(self.path) as c: rows = c.execute("SELECT id,title,source_project_id,version,metadata_json,status FROM templates WHERE status='published' ORDER BY title").fetchall()
        return [dict(zip(("id","title","source_project_id","version","metadata_json","status"), row)) for row in rows]

    def fork_template(self, actor: dict[str, Any], template_id: str, fork_project_id: str) -> dict[str, Any]:
        with connect(self.path) as c:
            if not c.execute("SELECT 1 FROM templates WHERE id=? AND status='published'", (template_id,)).fetchone(): raise PlatformError("not_found", "template not found", 404)
            item = {"id": uid(), "template_id": template_id, "user_id": actor["id"], "fork_project_id": fork_project_id, "created_at": now()}
            try: c.execute("INSERT INTO template_forks VALUES (?,?,?,?,?)", tuple(item.values()))
            except sqlite3.IntegrityError as exc: raise PlatformError("fork_exists", "this fork already exists", 409) from exc
            return item

    def create_exam(self, actor: dict[str, Any], prompt: str, version: str) -> dict[str, Any]:
        if actor["role"] not in {"admin", "client"}: raise PlatformError("forbidden", "exam management requires admin or client", 403)
        item = {"id": uid(), "prompt": prompt, "version": version, "is_active": 1, "created_at": now()}
        with connect(self.path) as c:
            c.execute("UPDATE certification_exams SET is_active=0")
            c.execute("INSERT INTO certification_exams VALUES (?,?,?,?,?)", tuple(item.values()))
        return item

    def active_exam(self) -> dict[str, Any] | None:
        with connect(self.path) as c: row = c.execute("SELECT id,prompt,version,created_at FROM certification_exams WHERE is_active=1 ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(zip(("id","prompt","version","created_at"), row)) if row else None

    def apply_certification(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        with connect(self.path) as c:
            exam = c.execute("SELECT id FROM certification_exams WHERE id=? AND is_active=1", (payload["exam_id"],)).fetchone()
            if not exam: raise PlatformError("exam_unavailable", "active exam not found", 404)
            submission = self.submit_artifact(actor, "certification_exam", payload["exam_id"], payload["artifact"])
            item = {"id": uid(), "exam_id": payload["exam_id"], "user_id": actor["id"], "submission_id": submission["id"], "status": "reviewing", "score": None, "feedback": None, "created_at": now()}
            c.execute("INSERT INTO certification_applications VALUES (?,?,?,?,?,?,?,?)", tuple(item.values()))
            c.execute("INSERT OR IGNORE INTO creator_certifications (id,user_id,status,level,source_type,submission_id,score,feedback,certified_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (uid(),actor["id"],"reviewing","standard","direct_exam",submission["id"],None,None,None,now()))
            return item

    def certification_status(self, actor: dict[str, Any]) -> dict[str, Any] | None:
        with connect(self.path) as c: row = c.execute("SELECT status,level,source_type,score,feedback,certified_at FROM creator_certifications WHERE user_id=?", (actor["id"],)).fetchone()
        return dict(zip(("status","level","source_type","score","feedback","certified_at"), row)) if row else None

    def review_certification(self, actor: dict[str, Any], application_id: str, approved: bool, score: float | None, feedback: str) -> dict[str, Any]:
        if actor["role"] not in {"admin", "reviewer"}: raise PlatformError("forbidden", "reviewer role required", 403)
        status = "certified" if approved else "rejected"
        with connect(self.path) as c:
            row = c.execute("SELECT user_id,submission_id FROM certification_applications WHERE id=?", (application_id,)).fetchone()
            if not row: raise PlatformError("not_found", "application not found", 404)
            c.execute("UPDATE certification_applications SET status=?,score=?,feedback=? WHERE id=?", (status,score,feedback,application_id))
            c.execute("UPDATE creator_certifications SET status=?,score=?,feedback=?,certified_at=? WHERE user_id=?", (status,score,feedback,now() if approved else None,row[0]))
            return {"application_id": application_id, "user_id": row[0], "status": status, "score": score, "feedback": feedback}
