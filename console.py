"""
console.py

Console output and prompts that stay usable when the batch runners drive
several profiles at once.

- profile_tag: a ContextVar the batch runners set to "[EMI_AUTO_3]" per
  profile. asyncio tasks and asyncio.to_thread() each copy the current
  context, so every line post.py / boost.py prints for that profile picks
  up its tag, with no tag argument threaded through every function. Single
  -profile runs never set it and print exactly as before.
- ask(): an async input() that runs in a worker thread (so a profile
  waiting on a human doesn't freeze every other profile on the event loop)
  and holds a lock (so two profiles never prompt over each other and
  Enter always answers the profile named in the prompt).
"""

from __future__ import annotations

import asyncio
import builtins
import threading
from contextvars import ContextVar

profile_tag: ContextVar[str] = ContextVar("profile_tag", default="")

_prompt_lock = threading.Lock()


def _tagged(text: str) -> str:
    tag = profile_tag.get()
    if not tag:
        return text
    # Keep leading blank lines above the tag rather than between tag and
    # text, and drop the single-run indentation -- the tag replaces it.
    stripped = text.lstrip("\n")
    return text[: len(text) - len(stripped)] + f"{tag} {stripped.lstrip(' ')}"


def tagged_print(*args, **kwargs) -> None:
    """Drop-in print() that prefixes the current profile's tag, if any."""
    if args and profile_tag.get():
        args = (_tagged(str(args[0])),) + args[1:]
    builtins.print(*args, **kwargs)


def _locked_input(prompt: str) -> str:
    with _prompt_lock:
        return builtins.input(prompt)


async def ask(prompt: str) -> str:
    """input() for async code: doesn't block the event loop, one prompt at a time."""
    return await asyncio.to_thread(_locked_input, _tagged(prompt))
