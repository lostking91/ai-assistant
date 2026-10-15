"""Per-skill Keychain credential storage used by the IDaaS v3 SDK."""

from typing import Dict, List, Optional, Tuple

try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
    _KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore
    KeyringError = Exception  # type: ignore
    PasswordDeleteError = Exception  # type: ignore
    _KEYRING_AVAILABLE = False


SERVICE_NAME = "skill_auth_code"
KEY_SKILLS_REGISTRY = "_skills"
LEGACY_KEYS = ("accessToken", "uuid")


def _require_backend() -> None:
    if not _KEYRING_AVAILABLE:
        raise RuntimeError("keyring library is not installed")
    try:
        keyring.get_keyring()
    except Exception as exc:
        raise RuntimeError(f"keychain backend is unavailable: {type(exc).__name__}: {exc}") from exc


def get(key: str) -> Optional[str]:
    _require_backend()
    try:
        return keyring.get_password(SERVICE_NAME, key)
    except KeyringError as exc:
        raise RuntimeError(f"keychain read failed: {exc}") from exc


def set(key: str, value: str) -> None:
    _require_backend()
    try:
        keyring.set_password(SERVICE_NAME, key, value)
    except KeyringError as exc:
        raise RuntimeError(f"keychain write failed: {exc}") from exc


def delete(key: str) -> None:
    _require_backend()
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except PasswordDeleteError:
        pass
    except KeyringError as exc:
        raise RuntimeError(f"keychain delete failed: {exc}") from exc


def _register_skill(skill_id: str) -> None:
    if not skill_id:
        return
    raw = get(KEY_SKILLS_REGISTRY) or ""
    skills = {value for value in raw.split(",") if value}
    if skill_id not in skills:
        skills.add(skill_id)
        set(KEY_SKILLS_REGISTRY, ",".join(sorted(skills)))


def _unregister_skill(skill_id: str) -> None:
    raw = get(KEY_SKILLS_REGISTRY) or ""
    skills = {value for value in raw.split(",") if value}
    if skill_id not in skills:
        return
    skills.discard(skill_id)
    if skills:
        set(KEY_SKILLS_REGISTRY, ",".join(sorted(skills)))
    else:
        delete(KEY_SKILLS_REGISTRY)


def list_skills() -> List[str]:
    raw = get(KEY_SKILLS_REGISTRY) or ""
    return sorted(value for value in raw.split(",") if value)


def _maybe_migrate_legacy(skill_id: str) -> None:
    if not skill_id:
        return
    try:
        if not any(get(key) is not None for key in LEGACY_KEYS):
            return
        if get(f"{skill_id}:accessToken") is not None or get(f"{skill_id}:uuid") is not None:
            return
        for key in LEGACY_KEYS:
            value = get(key)
            if value is not None:
                set(f"{skill_id}:{key}", value)
                delete(key)
        _register_skill(skill_id)
    except RuntimeError:
        return


def get_auth_bundle(claw_uid: Optional[str], skill_id: str) -> Tuple[Optional[str], Optional[str]]:
    if not skill_id:
        return None, None
    _maybe_migrate_legacy(skill_id)
    stored_claw = get(f"{skill_id}:clawUid")
    if stored_claw is None or (claw_uid and stored_claw != claw_uid):
        return None, None
    return get(f"{skill_id}:uuid"), get(f"{skill_id}:accessToken")


def save_auth_bundle(
    *,
    claw_uid: Optional[str] = None,
    skill_id: str,
    access_token: Optional[str] = None,
    auth_uuid: Optional[str] = None,
    clear_uuid: bool = False,
) -> bool:
    if not skill_id:
        return False
    try:
        _maybe_migrate_legacy(skill_id)
        if claw_uid:
            set(f"{skill_id}:clawUid", claw_uid)
        if access_token is not None:
            set(f"{skill_id}:accessToken", access_token)
        if clear_uuid:
            delete(f"{skill_id}:uuid")
        elif auth_uuid is not None:
            set(f"{skill_id}:uuid", auth_uuid)
        if any((claw_uid is not None, access_token is not None, auth_uuid is not None, get(f"{skill_id}:clawUid") is not None, get(f"{skill_id}:accessToken") is not None, get(f"{skill_id}:uuid") is not None)):
            _register_skill(skill_id)
        return True
    except RuntimeError:
        return False


def clear_all(skill_id: Optional[str] = None) -> int:
    skills = [skill_id] if skill_id else list_skills()
    deleted = 0
    for current_skill_id in skills:
        if not current_skill_id:
            continue
        for suffix in ("clawUid", "accessToken", "uuid"):
            try:
                if get(f"{current_skill_id}:{suffix}") is not None:
                    delete(f"{current_skill_id}:{suffix}")
                    deleted += 1
            except RuntimeError:
                continue
        try:
            _unregister_skill(current_skill_id)
        except RuntimeError:
            pass
    return deleted


def status(skill_id: Optional[str] = None) -> Dict[str, object]:
    skills = [skill_id] if skill_id else list_skills()
    return {"skills": {sid: {key: get(f"{sid}:{suffix}") is not None for key, suffix in (("claw_uid", "clawUid"), ("access_token", "accessToken"), ("uuid", "uuid"))} for sid in skills if sid}}


