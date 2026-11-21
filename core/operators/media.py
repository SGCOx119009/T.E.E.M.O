"""
core/operators/media.py — the operator for what plays.

WHAT PLAYS IS NOT WHAT DRIVES
    Video in the HUD, a summary of a YouTube video, and the music player are
    three different things: one is the screen, one is a text answer about a
    video, and one is another application. They were once confused — two
    tools claimed the same job, and the model could not tell which one to
    call, which is the bug the video work fixed.

    Grouping them here makes the distinction structural rather than a
    sentence in a description: the browser operator drives the browser, this
    one plays.

WHY YOUTUBE IS HERE AND NOT IN THE BROWSER
    A YouTube link can be opened in a browser OR played in the HUD, and those
    are not the same action. The tool that plays it says so; the tool that
    browses says so. Putting them in different operators keeps that
    difference visible in the shape of the product and not only in its words.
"""
from __future__ import annotations

from core.operators.base import Operator


class MediaOperator(Operator):
    name = "media"
    description = (
        "What plays: video in the HUD, YouTube, and music."
    )
    context = "media"
    risk_class = "high"
    tools = (
        "video_player",
        "youtube_video",
        "spotify_control",
    )
