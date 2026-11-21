"""
core/operators/vision.py — the operator for what the assistant can see.

THE SMALLEST OPERATOR, AND THE ONE WITH THE CLEAREST LIMIT
    Two tools: a screen capture and the camera. Everything else the
    assistant "sees" comes through one of them, and both are marked in the
    prompt as not continuous — the assistant sees a frame when it asks for
    one, and nothing in between.

WHY IT IS ITS OWN OPERATOR
    Being able to see is not a property of every session. An engine without
    vision has these tools withheld, and the model is told what it cannot
    do, so it never narrates a screen it never received. That is why vision
    is a domain and not a flag: a domain can be switched off whole.

THE LATER PHASE
    This operator is where the Set-of-Mark grounding arrives: the screen
    becomes a list of numbered elements, and the model answers with an
    element instead of a pixel. The contract in core/operators/base.py is
    already the shape that needs — a closed vocabulary, a normalised target,
    and arguments checked against a schema.
"""
from __future__ import annotations

from core.operators.base import Operator


class VisionOperator(Operator):
    name = "vision"
    description = (
        "What the assistant can see: a screenshot of the screen, and the "
        "camera."
    )
    context = "vision"
    risk_class = "read_or_navigate"
    tools = (
        "screen_process",
        "close_camera",
    )
