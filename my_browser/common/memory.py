"""Hand freed memory back to the system after heavy work.

CPython frees objects, but glibc's malloc keeps the freed pages for later, so after a research or a news collection
the process stays at its peak: on Railway it climbed to about 1 GB within hours of a deploy and stayed there
(25.10.2026). On Linux, threads that read page-sized texts and dropped them left a process at 1.2 GB; malloc_trim(0)
brought it to 15 MB. Without glibc (macOS) trimming is a no-op.
"""

import ctypes
import gc

try:
    _malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
    _malloc_trim.argtypes = [ctypes.c_size_t]
    _malloc_trim.restype = ctypes.c_int
except (OSError, AttributeError):  # not glibc
    _malloc_trim = None


def trim() -> None:
    """Give glibc's free pages back to the system. ctypes lets go of the GIL meanwhile, so the web app runs on."""
    if _malloc_trim:
        _malloc_trim(0)


def release() -> None:
    """After a research or a news run: collect cyclic garbage (parse trees are full of cycles), then trim.
    The collection holds the GIL for a moment (about 50 ms with every part loaded), so not after every small step."""
    gc.collect()
    trim()
