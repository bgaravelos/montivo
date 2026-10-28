"""Output frames: the 9:16 cut and the feed formats rendered from the same EDL (4:5 for the Instagram / Facebook feed
and LinkedIn, 1:1 square).

The EDL's `target.aspect` names the frame (default "9:16"). Every aspect is 1080 wide, so x needs no scaling. Every y
the EDL and the caption constants hold is in the 1080x1920 frame (CAPTION_TOP, SAFE_TOP / SAFE_BOTTOM, a fixed look
top, a pinned callout, a forced caption top): the crop is the full source height in every aspect, so a source row lands
at the same fraction of the output height whatever the aspect, and a y scaled by height / 1920 keeps its place against
the face. The 9:16 frame scales by exactly 1, so its pixels do not change.

The safe band is the exception: 9:16 keeps clear of the Reels / TikTok / Shorts UI drawn over the video, a feed post
has its UI outside the picture, so a feed aspect only keeps FEED_MARGIN of its height free at the top and the bottom
(on run 015 the 1920 band, scaled, pushed every 4:5 caption above the head; this one lets them sit on the chest).
"""
from __future__ import annotations

from dataclasses import dataclass

BASE_W, BASE_H = 1080, 1920
ASPECTS: dict[str, tuple[int, int]] = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
DEFAULT_ASPECT = "9:16"
FEED_MARGIN = 0.06


@dataclass(frozen=True)
class Frame:
    aspect: str = DEFAULT_ASPECT

    @property
    def w(self) -> int:
        return ASPECTS[self.aspect][0]

    @property
    def h(self) -> int:
        return ASPECTS[self.aspect][1]

    @property
    def ratio(self) -> tuple[int, int]:
        a, b = self.aspect.split(":")
        return int(a), int(b)

    @property
    def tag(self) -> str:
        """For file names: 9x16, 4x5, 1x1."""
        return self.aspect.replace(":", "x")

    def band(self, top: int, bottom: int) -> tuple[int, int]:
        """The (top, bottom) captions and callouts keep inside: the 9:16 band as given, a feed aspect's margins."""
        if self.aspect == DEFAULT_ASPECT:
            return top, bottom
        m = int(round(FEED_MARGIN * self.h))
        return m, self.h - m

    def y(self, v: float) -> int:
        """A y of the 1080x1920 frame in this frame."""
        return int(v) if self.h == BASE_H else int(round(float(v) * self.h / BASE_H))


FRAME_916 = Frame()


def frame_of(aspect: str | None) -> Frame:
    a = (aspect or DEFAULT_ASPECT).strip()
    if a not in ASPECTS:
        raise ValueError(f"aspect {a!r}: one of {', '.join(ASPECTS)}")
    return Frame(a)


def frame_for(edl: dict | None) -> Frame:
    """The frame an EDL renders to (`target.aspect`)."""
    return frame_of(((edl or {}).get("target") or {}).get("aspect"))


def with_aspect(edl: dict, aspect: str) -> dict:
    """`edl` (changed in place and returned) set to render in `aspect`."""
    f = frame_of(aspect)
    edl["target"] = {"aspect": f.aspect, "width": f.w, "height": f.h}
    return edl
