"""
Filesystem operations with Node's semantics, where Python's differ.

Ported from `reference/claude-code/src/utils/fsOperations.ts` -> `NodeFsOperations.rm` (Node's
`fs.rm`). Only `rm` is ported: upstream's plugin code removes paths that may be a directory, a
file or a symlink with one `rm(path, {recursive: true, force: true})`, while `shutil.rmtree`
refuses files and symlinks and `ignore_errors=True` hides real failures.
"""

from __future__ import annotations

import os
import shutil


def rm(path: str, *, recursive: bool = False, force: bool = False) -> None:
    """
    `fs.rm`: remove a file or symlink (never its target), or, with `recursive`, a directory
    tree. With `force` a missing path is not an error. Any other failure raises.
    """
    if not os.path.lexists(path):
        if force:
            return
        raise FileNotFoundError(2, "No such file or directory", path)
    if os.path.isdir(path) and not os.path.islink(path):
        if not recursive:
            raise IsADirectoryError(21, "Is a directory", path)
        shutil.rmtree(path)
        return
    os.unlink(path)


__all__ = ["rm"]
