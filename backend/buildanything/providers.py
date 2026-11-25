"""AI provider keys. Keys live in the OS keychain and are never returned or logged."""
from __future__ import annotations

import os
import sys
from typing import Awaitable, Callable, Protocol

SERVICE = "buildanything"

PROVIDERS = {
    "openai": {"name": "OpenAI", "key": "OPENAI_API_KEY", "built": True, "note": ""},
    "anthropic": {"name": "Anthropic", "key": "BUILDANYTHING_ANTHROPIC_API_KEY", "built": False, "note": "Planned as the second provider."},
}


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringStore:
    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


def _environment(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                return winreg.QueryValueEx(reg, name)[0]
        except OSError:
            return None
    return None


def find_key(store: SecretStore, name: str) -> tuple[str | None, str]:
    """Return the key and where it came from. The keychain wins over the environment."""
    value = store.get(name)
    if value:
        return value, "keychain"
    value = _environment(name)
    if value:
        return value, "environment"
    return None, "none"


def valid_key(value: str) -> bool:
    return 10 <= len(value) <= 500 and not any(c.isspace() for c in value)


ModelLister = Callable[[str], Awaitable[list[str]]]


async def list_openai_models(key: str) -> list[str]:
    """Free call: listing models uses no tokens."""
    from openai import AsyncOpenAI

    async with AsyncOpenAI(api_key=key, timeout=20) as client:
        return [m.id async for m in client.models.list()]


async def check_provider(key: str | None, tiers: dict[str, str], lister: ModelLister) -> dict:
    if not key:
        return {"ok": False, "detail": "No key is set."}
    try:
        available = set(await lister(key))
    except Exception as exc:  # The message could echo request details, so report only its type.
        return {"ok": False, "detail": f"The provider rejected the check ({type(exc).__name__})."}
    missing = {tier: model for tier, model in tiers.items() if model not in available}
    if missing:
        return {"ok": False, "detail": "Key works, but these models are unavailable: " + ", ".join(missing.values()), "missing": missing}
    return {"ok": True, "detail": "Key works, and every tier's model is available."}
