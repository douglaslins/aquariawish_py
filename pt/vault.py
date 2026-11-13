"""Secrets (the HoYoLAB cookie) in the system keyring: the Windows Credential Manager, or the keyring of the desktop on other systems.

`keyring` is optional. Without it nothing is stored and `save` returns False. A Credential Manager entry is limited to about 1280
characters, and a cookie can be longer, so the value is stored in pieces: `<name>:0`, `<name>:1` ... and the number of pieces in `<name>:n`.
The value is never written to the log.
"""
from __future__ import annotations

import logging

log = logging.getLogger("pt.vault")

SERVICE = "PullsTracker"
PIECE = 600
# keyring's two stand-ins for "no usable backend" are keyring.backends.fail.Keyring and keyring.backends.null.Keyring: the class is named
# Keyring in both, so they can only be told apart by their module.
_NO_BACKEND_MODULES = ("fail", "null")


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:      # noqa: BLE001 - not installed, or no backend can be loaded
        return None


def available() -> bool:
    k = _keyring()
    if k is None:
        return False
    try:
        return type(k.get_keyring()).__module__.rsplit(".", 1)[-1] not in _NO_BACKEND_MODULES
    except Exception:      # noqa: BLE001
        return False


def _count(k, name: str) -> int:
    try:
        return int(k.get_password(SERVICE, f"{name}:n") or 0)
    except Exception:      # noqa: BLE001 - a locked or failing keyring counts as "nothing stored"
        return 0


def delete(name: str) -> bool:
    """Remove every piece of `name`. True when nothing of it is left (each piece that could not be deleted is read back: one that is
    already gone is fine); False when the keyring failed or refused, so a caller never reports a removal that did not happen."""
    k = _keyring()
    if k is None:
        return True            # no keyring: nothing of it can be stored
    try:
        n = int(k.get_password(SERVICE, f"{name}:n") or 0)
    except ValueError:         # a damaged count: no piece can be found by it; the count itself is removed below
        n = 0
    except Exception:      # noqa: BLE001 - a locked or failing keyring: nothing was removed, and that is said
        log.warning("The keyring could not be read to remove %s", name)
        return False
    clean = True
    for key in [f"{name}:{i}" for i in range(n)] + [f"{name}:n"]:
        try:
            k.delete_password(SERVICE, key)
        except Exception:      # noqa: BLE001
            try:
                clean = clean and k.get_password(SERVICE, key) is None
            except Exception:      # noqa: BLE001
                clean = False
    if not clean:
        log.warning("Part of %s could not be removed from the keyring", name)
    return clean


def save(name: str, value: str) -> bool:
    """Store `value` (an empty value deletes it). False when there is no keyring or it refused (also when the old value could not be
    removed: its pieces would stay in the keyring beside the new ones)."""
    k = _keyring()
    if k is None or not available():
        return False
    try:
        if not delete(name):
            return False
        if not value:
            return True
        pieces = [value[i:i + PIECE] for i in range(0, len(value), PIECE)]
        for i, piece in enumerate(pieces):
            k.set_password(SERVICE, f"{name}:{i}", piece)
        k.set_password(SERVICE, f"{name}:n", str(len(pieces)))
        return True
    except Exception as e:      # noqa: BLE001
        log.warning("The keyring did not take the value of %s (%s)", name, type(e).__name__)
        return False


def load(name: str) -> str:
    k = _keyring()
    if k is None:
        return ""
    try:
        pieces = [k.get_password(SERVICE, f"{name}:{i}") for i in range(_count(k, name))]
    except Exception:      # noqa: BLE001
        return ""
    # A missing piece (an interrupted save, or an entry removed by hand) would give a cut cookie that the service rejects with a confusing
    # error: no value is better than half of one.
    return "" if any(p is None for p in pieces) else "".join(pieces)
