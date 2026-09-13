"""Fail-closed resolution of opaque IMAP secret references."""
import os
import re
import stat
from pathlib import Path

from django.conf import settings


SECRET_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")


class ImapSecretError(RuntimeError):
    """A stable, non-sensitive secret resolution failure."""

    code = "imap_secret_unavailable"


def validate_secret_ref(secret_ref):
    value = str(secret_ref or "")
    if not SECRET_REF_RE.fullmatch(value) or value in {".", ".."}:
        raise ImapSecretError()
    return value


def resolve_imap_secret(secret_ref):
    """Read a root-owned/mounted secret without exposing its path or value."""
    reference = validate_secret_ref(secret_ref)
    configured = str(getattr(settings, "SOOI_IMAP_SECRET_DIR", "") or "")
    root = Path(configured)
    if not configured or not root.is_absolute() or not root.is_dir():
        raise ImapSecretError()

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        file_fd = os.open(reference, flags, dir_fd=directory_fd)
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ImapSecretError()
            value = os.read(file_fd, 65537)
            if not value or len(value) > 65536:
                raise ImapSecretError()
            try:
                secret = value.decode("utf-8").rstrip("\r\n")
                if not secret:
                    raise ImapSecretError()
                return secret
            except UnicodeDecodeError as exc:
                raise ImapSecretError() from exc
        finally:
            os.close(file_fd)
    except (OSError, ValueError) as exc:
        raise ImapSecretError() from exc
    finally:
        os.close(directory_fd)
