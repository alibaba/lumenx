"""Private media scratch ownership on the existing macOS/Linux media runtime.

Callers decide which verified file still needs retention. This helper only owns
scratch cleanup; it never makes an artifact authoritative or retries execution.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile

logger = logging.getLogger(__name__)


class OwnedPrivateMediaDirectory:
    """Pin the created directory so cleanup cannot traverse a replacement root."""

    def __init__(self, *, prefix: str):
        self.path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        self._fd: int | None = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(self._fd, 0o700)
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise

    def assert_owned(self) -> None:
        if self._fd is None:
            raise OSError("Private media directory is closed")
        owned = os.fstat(self._fd)
        current = os.stat(self.path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
            raise OSError("Private media directory ownership changed")

    def cleanup(self, *, keep_name: str | None = None) -> None:
        """Best effort, without masking an execution or persistence exception.

        Lost ownership or a filesystem error leaves the remaining private files
        in place. They are not public media and no recovery scan is implied.
        """
        if self._fd is None:
            return
        try:
            self.assert_owned()
            for name in os.listdir(self._fd):
                if name == keep_name:
                    continue
                child = os.stat(name, dir_fd=self._fd, follow_symlinks=False)
                if stat.S_ISDIR(child.st_mode):
                    shutil.rmtree(name, dir_fd=self._fd)
                else:
                    os.unlink(name, dir_fd=self._fd)
            if keep_name is None:
                self.assert_owned()
                os.rmdir(self.path)
            else:
                logger.warning("Retained private media at %s", self.path / keep_name)
        except OSError:
            logger.warning("Could not safely clean private media directory %s", self.path)
        finally:
            try:
                os.close(self._fd)
            except OSError:
                logger.warning("Could not close private media directory %s", self.path)
            self._fd = None
