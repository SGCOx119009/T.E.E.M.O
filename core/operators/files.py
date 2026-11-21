"""
core/operators/files.py — the operator for what is stored.

FILES ARE NOT THE DESKTOP
    Moving a file, reading a document and building a project are one domain,
    and it is a different one from "what is on the screen". Keeping them apart
    matters for two reasons: a file tool that fails has to say so about the
    FILE, not about the window; and the memory tools live here too, because
    what the assistant remembers is stored locally and read back by a local
    search — a file operation with a different name.

WHAT IT OWNS
    The controllers and processors of local files, the two coding tools, and
    the memory pair.

WHY THE MEMORY TOOLS ARE HERE
    `save_memory` writes a local file and `recall_memory` searches it. They
    are not about the screen and not about the web, and putting them in the
    desktop operator would make "remember this" sound like a mouse action.
"""
from __future__ import annotations

from core.operators.base import Operator


class FilesOperator(Operator):
    name = "files"
    description = (
        "What is stored: reading, writing, moving and organising local files, "
        "working on code, and the assistant's own memory."
    )
    context = "files"
    risk_class = "high"
    tools = (
        "file_controller",
        "file_processor",
        "code_helper",
        "dev_agent",
        "save_memory",
        "recall_memory",
    )
