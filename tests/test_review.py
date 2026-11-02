"""Tests for lazycomics.review — golden-subset checks and contact sheets.

Checks are driven by hand-written layout / slot records (the shapes
``text_renderer`` and ``assembler`` write), so each failure mode is pinned
down exactly. Sheet tests are smoke tests: valid PNG, sensible size, no
crash on missing inputs.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import review  # noqa: E402
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text("## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> x\n")
        self.project = create_project("p1", cbml, base_dir=self.root)
        self.slots: dict = {}

    def panel(self, pid: str, w: int, h: int, *, aspect: float, layout=None,
              characters=None, lettered: bool = True, visible="whole", pages=None,
              scale: float = 1.0):
        """Write a panel + its enriched JSON, lettered PNG, layout and slot record.

        ``visible="whole"`` records the whole panel as shown by its slot;
        ``None`` writes no slot record (as if never assembled). Its page is
        written 1000px tall unless one exists, so a tail's minimum size on
        the page is 12px long and 6px wide at ``scale`` 1.
        """
        Image.new("RGB", (w, h), (90, 120, 150)).save(self.project.panels_dir / f"{pid}.png")
        (self.project.enriched_dir / f"{pid}.json").write_text(json.dumps({
            "panel_id": pid, "aspect_ratio": aspect, "characters": characters or [],
        }))
        if lettered:
            Image.new("RGB", (w, h), (200, 200, 200)).save(self.project.panels_text_dir / f"{pid}.png")
        if layout is not None:
            (self.project.panels_text_dir / f"{pid}.layout.json").write_text(json.dumps(layout))
        if visible is not None:
            page = int(pid.split("_")[1])
            self.slots[pid] = {
                "visible": [0, 0, w, h] if visible == "whole" else list(visible),
                "pages": pages or [page],
                "scale": scale,
            }
            (self.project.base_dir / "panel_slots.json").write_text(json.dumps(self.slots))
            for n in pages or [page]:
                if not (self.project.pages_dir / f"page_{n:03d}.png").is_file():
                    self.page(n, h=1000)

    def page(self, n: int, w: int = 400, h: int = 600):
        Image.new("RGB", (w, h), (30, 30, 30)).save(self.project.pages_dir / f"page_{n:03d}.png")

    def close(self):
        self.tmp.cleanup()


def _with_env(fn):
    def wrapper():
        env = _Env()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


def _bubble(line, rect, *, speaker="A", type_="speech", tail=True, anchor=None,
            no_tail="unmatched"):
    """A layout-record bubble. ``tail=True`` gives it a 20x20px triangle off
    the middle of its bottom edge; a dict is used as the tail as given."""
    b = {"line": line, "speaker": speaker, "type": type_, "rect": rect,
         "anchor": anchor or [0, 0], "tail": tail}
    if tail is True:
        cx, y = (rect[0] + rect[2]) // 2, rect[3]
        b["tail"] = {"points": [[cx - 10, y], [cx + 10, y], [cx, y + 20]]}
    elif not tail:
        b["tail"], b["no_tail"] = None, no_tail
    return b


def _messages(env, pid, *kinds):
    """Issue messages for ``pid``, only of ``kinds`` if any are given."""
    return [str(i) for i in review.check_panel(env.project, pid) if not kinds or i.kind in kinds]


P = "page_1_panel_1"


# ---------------------------------------------------------------------------
# check_panel
# ---------------------------------------------------------------------------


@_with_env
def test_clean_panel_has_no_issues(env):
    env.panel(P, 448, 1024, aspect=0.44, layout={
        "size": [448, 1024], "font_px": 20,
        "bubbles": [_bubble(0, [20, 20, 200, 80], type_="shout", anchor=[100, 400]),
                    _bubble(1, [230, 120, 420, 180], anchor=[100, 400])],
        "faces": [[50, 350, 150, 450]],
    })
    assert review.check_panel(env.project, P) == []


@_with_env
def test_wrong_aspect_flagged(env):
    # The pre-W1 symptom: a 0.44 slot generated at 784x576.
    env.panel(P, 784, 576, aspect=0.44, layout={"size": [784, 576]})
    issues = review.check_panel(env.project, P)
    assert [i.kind for i in issues] == ["aspect"]
    assert "aspect 1.36" in str(issues[0]) and "0.44" in str(issues[0])


@_with_env
def test_box_cut_off_by_slot_flagged(env):
    # Cover crop keeps x 192-592 of a 784-wide panel: a bubble at x 20 and a
    # caption at the right edge fall outside; the centred one survives.
    env.panel(P, 784, 576, aspect=1.36, visible=[192, 0, 592, 576], layout={
        "bubbles": [_bubble(0, [20, 20, 200, 80], tail=False, type_="shout"),
                    _bubble(1, [250, 200, 500, 260], tail=False, type_="shout")],
        "captions": [[600, 20, 760, 60]],
    })
    assert _messages(env, P, "slot") == [
        "line 1 is cut off by its page slot (slot shows x 192-592, y 0-576)",
        "caption 1 is cut off by its page slot (slot shows x 192-592, y 0-576)",
    ]


@_with_env
def test_missing_slot_record_reported(env):
    env.panel(P, 448, 1024, aspect=0.44, layout={}, visible=None)
    assert _messages(env, P) == ["no page-slot record (run assemble_pages)"]


@_with_env
def test_reversed_reading_order_flagged(env):
    # page_3_panel_1 (2026-06-10): each later line placed above the last.
    env.panel(P, 1104, 832, aspect=1.33, layout={"bubbles": [
        _bubble(0, [500, 500, 860, 540], speaker="YUKI", tail=False, type_="shout"),
        _bubble(1, [410, 430, 850, 470], speaker="GERALD", tail=False, type_="shout"),
        _bubble(2, [700, 200, 890, 245], speaker="YUKI", tail=False, type_="shout"),
        _bubble(3, [140, 95, 360, 140], speaker="GERALD", tail=False, type_="shout"),
    ]})
    assert _messages(env, P, "order") == [
        "reading order: line 2 sits above line 1",
        "reading order: line 3 sits above line 2",
        "reading order: line 4 sits above line 3",
    ]


@_with_env
def test_same_row_right_to_left_flagged(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={"bubbles": [
        _bubble(0, [600, 40, 900, 100], tail=False, type_="shout"),
        _bubble(1, [100, 50, 400, 110], tail=False, type_="shout"),   # same row, left
    ]})
    assert _messages(env, P, "order") == ["reading order: line 2 sits left of line 1 in the same row"]


@_with_env
def test_same_row_left_to_right_is_fine(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={"bubbles": [
        _bubble(0, [100, 50, 400, 110], tail=False, type_="shout"),
        _bubble(1, [600, 40, 900, 100], tail=False, type_="shout"),
    ]})
    assert _messages(env, P, "order") == []


@_with_env
def test_overlaps_flagged(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={
        "bubbles": [_bubble(0, [100, 100, 400, 160], tail=False, type_="shout"),
                    _bubble(1, [300, 300, 600, 360], tail=False, type_="shout")],
        "captions": [[350, 320, 700, 380]],
        "sfx": [[600, 500, 760, 560]],
        "faces": [[120, 110, 180, 170], [620, 490, 700, 570]],
    })
    assert _messages(env, P, "overlap", "face") == [
        "overlap: line 2 and caption 1", "line 1 covers a face", "sfx 1 covers a face"]


@_with_env
def test_corner_touch_not_flagged_as_overlap(env):
    # Golden run 2026-10-30, page_10_panel_1: rects meet in a 4x7px corner
    # where the rounded bubbles don't visibly touch.
    env.panel(P, 224, 1024, aspect=0.22, layout={"bubbles": [
        _bubble(0, [113, 315, 204, 375], tail=False, type_="shout"),
        _bubble(1, [20, 368, 117, 407], tail=False, type_="shout"),
    ]})
    assert _messages(env, P, "overlap") == []


@_with_env
def test_missing_tail_reports_its_cause_for_every_type(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={"bubbles": [
        _bubble(0, [100, 50, 400, 110], speaker="NOVA", tail=False),
        _bubble(1, [100, 200, 400, 260], type_="shout", tail=False, no_tail="type"),
        _bubble(2, [100, 350, 400, 410], type_="thought", tail=False, no_tail="level"),
        _bubble(3, [100, 500, 400, 560], type_="whisper", tail=False, no_tail=None),
    ]})
    assert _messages(env, P, "no_tail") == [
        "line 1 (NOVA): no tail, speaker not matched to a face",
        "line 2 (A): no tail, shouts and whispers get none",
        "line 3 (A): no tail, bubble level with the speaker's face",
        "line 4 (A): no tail, reason not recorded (re-run render_text)",
    ]


@_with_env
def test_overflowing_dialogue_reported(env):
    env.panel(P, 448, 1024, aspect=0.44, layout={"overflow": 1.31, "bubbles": []})
    assert _messages(env, P, "overflow") == [
        "too much dialogue for the panel: stacked, the bubbles need 131% of its height"]


@_with_env
def test_level_consecutive_bubbles_flagged_for_stagger(env):
    # e2e page_3_panel_1: "I have seen worse crashes." and "Like 2008?" start
    # at the same height, so Gerald's next line, stacked under his first,
    # reads as part of it.
    env.panel(P, 912, 1024, aspect=0.89, layout={"font_px": 31, "bubbles": [
        _bubble(0, [380, 380, 750, 440], tail=False),
        _bubble(1, [50, 697, 495, 755], tail=False),
        _bubble(2, [550, 697, 740, 755], tail=False),
        _bubble(3, [128, 772, 352, 830], tail=False),
    ]})
    assert _messages(env, P, "stagger") == [
        "stagger: line 3 starts level with line 2 (0px lower, want 23+)"]


@_with_env
def test_tail_crossing_another_box_flagged(env):
    # e2e page_3_panel_1: "Much earlier." sits under "I have seen worse
    # crashes." and its tail runs up through it toward Gerald.
    up = {"points": [[230, 772], [244, 772], [240, 730]]}
    env.panel(P, 912, 1024, aspect=0.89, layout={
        "bubbles": [_bubble(0, [50, 697, 495, 755], tail=False),
                    _bubble(1, [128, 772, 352, 830], tail=up)],
        "captions": [[20, 20, 200, 60]],
    })
    assert _messages(env, P, "tail_cross") == ["tail of line 2 crosses line 1"]


@_with_env
def test_thought_dots_crossing_a_box_flagged(env):
    dots = {"root": [200, 110], "dots": [[200, 140, 8], [200, 170, 6]]}
    env.panel(P, 1024, 768, aspect=1.33, layout={
        "bubbles": [_bubble(0, [100, 50, 400, 110], type_="thought", tail=dots)],
        "sfx": [[150, 165, 300, 200]],
    })
    assert _messages(env, P, "tail_cross") == ["tail of line 1 crosses sfx 1"]


@_with_env
def test_tail_too_small_on_the_page_flagged(env):
    # A 14px-wide, 38px-long tail on a panel shown at 0.27 scale (the old
    # 800px e2e pages) is ~4px wide on the page; at 1.0 it reads.
    tail = {"points": [[193, 110], [207, 110], [200, 148]]}
    layout = {"bubbles": [_bubble(0, [100, 50, 400, 110], tail=tail)]}
    env.panel(P, 1024, 768, aspect=1.33, layout=layout, scale=0.27)
    assert _messages(env, P, "tail_size") == [
        "line 1: tail 10px long, 4px wide on the page (want 12+ and 6+)"]
    env.panel(P, 1024, 768, aspect=1.33, layout=layout, scale=1.0)
    assert _messages(env, P, "tail_size") == []


def test_tail_dims_for_triangles_and_dot_trails():
    w, l = review._tail_dims({"points": [[0, 0], [20, 0], [10, 30]]})
    assert (w, l) == (20, 30)
    w, l = review._tail_dims({"root": [0, 0], "dots": [[0, 10, 7], [0, 25, 5], [0, 36, 3]]})
    assert (w, l) == (14, 39)


@_with_env
def test_missing_page_scale_reported(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={"bubbles": [_bubble(0, [100, 50, 400, 110])]})
    del env.slots[P]["scale"]
    (env.project.base_dir / "panel_slots.json").write_text(json.dumps(env.slots))
    assert _messages(env, P, "record") == ["no page scale for this panel (re-run assemble_pages)"]


@_with_env
def test_tail_aimed_off_face_flagged(env):
    env.panel(P, 1024, 768, aspect=1.33, layout={
        "bubbles": [_bubble(0, [100, 50, 400, 110], anchor=[150, 400]),     # on the face
                    _bubble(1, [500, 50, 800, 110], anchor=[900, 600])],    # on nothing
        "faces": [[100, 350, 200, 450]],
    })
    assert _messages(env, P, "tail_anchor") == ["line 2: tail aims at no detected face"]


@_with_env
def test_missing_inputs_reported(env):
    assert _messages(env, "page_9_panel_9") == ["no generated panel"]
    env.panel(P, 448, 1024, aspect=0.44)  # no layout sidecar
    assert _messages(env, P) == ["no layout record (re-run render_text)"]


# ---------------------------------------------------------------------------
# Contact sheets
# ---------------------------------------------------------------------------


@_with_env
def test_pages_sheet_groups_by_page_with_assembled_page(env):
    env.panel("page_1_panel_1", 448, 1024, aspect=0.44, layout={})
    env.panel("page_1_panel_2", 784, 576, aspect=1.36, layout={}, visible=[192, 0, 592, 576])
    env.panel("page_2_panel_1", 1024, 512, aspect=2.0, layout={}, pages=[2, 3])
    for n in (1, 2, 3):
        env.page(n)
    out = review.pages_sheet(env.project, ["page_2_panel_1", "page_1_panel_2", "page_1_panel_1"],
                             env.root / "sheet.png", baseline=env.project,
                             issues={"page_1_panel_1": [review.Issue("order", "something wrong")]})
    sheet = Image.open(out)
    # Page 1 block holds two rows (the 0.44 one 450px tall), page 2 one row.
    assert sheet.height > 450 + 240
    # Four tile columns (raw | lettered | on page | baseline) plus the page.
    assert sheet.width > 4 * 200


@_with_env
def test_pages_sheet_tolerates_missing_inputs(env):
    env.panel("page_1_panel_1", 448, 1024, aspect=0.44, lettered=False, visible=None)
    out = review.pages_sheet(env.project, ["page_1_panel_1"], env.root / "sheet.png")
    assert Image.open(out).size[0] > 0


@_with_env
def test_character_sheet_one_row_per_character(env):
    ref = env.root / "gerald.png"
    Image.new("RGB", (300, 450), (10, 10, 10)).save(ref)
    gerald = {"identifier": "GERALD", "reference_images": [str(ref)]}
    yuki = {"identifier": "YUKI", "reference_images": []}  # no ref -> placeholder
    env.panel("page_1_panel_1", 448, 1024, aspect=0.44, characters=[gerald], layout={})
    env.panel("page_1_panel_2", 1024, 768, aspect=1.33, characters=[gerald, yuki], layout={})
    orig = review._confident_faces
    review._confident_faces = lambda img: [[150, 200, 250, 300]]
    try:
        out = review.character_sheet(env.project, ["page_1_panel_1", "page_1_panel_2"],
                                     env.root / "chars.png", tile_h=100)
    finally:
        review._confident_faces = orig
    sheet = Image.open(out)
    # Two rows (GERALD with 2 appearances, YUKI with 1), each ~100px tiles + labels.
    assert 2 * 100 < sheet.height < 2 * 220


def _match(env, pid, regions):
    """Record ``regions`` as render_text would, fingerprinted to the panel image."""
    path = env.project.enriched_dir / f"{pid}.json"
    data = json.loads(path.read_text())
    data["actual_character_regions"] = regions
    data["detected_faces_for"] = hashlib.sha1(
        (env.project.panels_dir / f"{pid}.png").read_bytes()).hexdigest()
    path.write_text(json.dumps(data))


@_with_env
def test_appearance_tile_crops_on_the_characters_matched_face(env):
    gerald = {"identifier": "GERALD"}
    yuki = {"identifier": "YUKI"}
    env.panel(P, 1024, 768, aspect=1.33, characters=[gerald, yuki], layout={})
    _match(env, P, {"GERALD": [100, 100, 200, 200], "YUKI": [700, 300, 750, 350]})
    # Crop spans 3x the face side across, 3.2x down (see _head_crop); label adds 22px.
    assert review._appearance_tile(env.project, P, "GERALD", 320).size == (300, 342)
    assert review._appearance_tile(env.project, P, "YUKI", 320).size == (300, 342)


@_with_env
def test_appearance_tile_ignores_regions_from_another_image(env):
    env.panel(P, 1024, 768, aspect=1.33,
              characters=[{"identifier": "GERALD"}, {"identifier": "YUKI"}], layout={})
    _match(env, P, {"GERALD": [100, 100, 200, 200]})
    # Panel regenerated since: the recorded face may now be anything.
    Image.new("RGB", (1024, 768), (1, 2, 3)).save(env.project.panels_dir / f"{P}.png")
    tile = review._appearance_tile(env.project, P, "GERALD", 240)
    # The whole 4:3 panel (320 wide at 240 high), not a 225-wide head crop.
    assert tile.height == 262 and tile.width >= 320


@_with_env
def test_character_sheet_stacks_baseline_crop_under_each_appearance(env):
    gerald = {"identifier": "GERALD"}
    env.panel(P, 448, 1024, aspect=0.44, characters=[gerald, {"identifier": "YUKI"}], layout={})
    _match(env, P, {"GERALD": [100, 100, 200, 200]})
    other = _Env()
    try:
        other.panel(P, 448, 1024, aspect=0.44, characters=[gerald], layout={})
        _match(other, P, {"GERALD": [100, 100, 200, 200]})
        alone = Image.open(review.character_sheet(env.project, [P], env.root / "a.png", tile_h=100))
        paired = Image.open(review.character_sheet(env.project, [P], env.root / "b.png",
                                                   baseline=other.project, tile_h=100))
    finally:
        other.close()
    # Each appearance tile is now two crops (+ label) high.
    assert paired.height - alone.height >= 2 * (100 + 22)


def test_confident_faces_drops_low_scores():
    from lazycomics import face_detector
    orig = face_detector.detect_faces
    face_detector.detect_faces = lambda img: [
        face_detector.FaceBox(291, 751, 357, 844, 0.53),   # page_6_panel_1's floor hit
        face_detector.FaceBox(105, 228, 336, 556, 0.76),
    ]
    try:
        assert review._confident_faces(Image.new("RGB", (448, 1024))) == [[105, 228, 336, 556]]
    finally:
        face_detector.detect_faces = orig


def test_head_crop_centres_on_largest_face():
    img = Image.new("RGB", (1000, 1000))
    crop = review._head_crop(img, [[100, 100, 120, 120], [500, 400, 600, 500]])
    # Largest face is 100px: crop spans 1.5s either side, 1.2s above, 2s below.
    assert crop.size == (300, 320)
    assert review._head_crop(img, []).size == (1000, 1000)


def testpanel_sort_key_orders_pages_numerically():
    ids = ["page_10_panel_1", "page_2_panel_2", "page_2_panel_10", "page_2_panel_1"]
    assert sorted(ids, key=review.panel_sort_key) == [
        "page_2_panel_1", "page_2_panel_2", "page_2_panel_10", "page_10_panel_1",
    ]


def test_dataset_sheet_wraps_into_rows():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        images = []
        for i in range(9):
            p = tmp / f"GERALD_{i:02d}.png"
            Image.new("RGB", (100, 100), (i * 20, 0, 0)).save(p)
            images.append(p)
        out = review.dataset_sheet(images, tmp / "sheet.png", title="GERALD", per_row=4, tile_h=50)
        with Image.open(out) as sheet:
            # 3 rows of 4, 4, 1 tiles: wider than 4 tiles, taller than 3 rows of tiles.
            assert sheet.width >= 4 * 50 and sheet.height > 3 * 50
        empty = review.dataset_sheet([], tmp / "empty.png", title="none yet")
        with Image.open(empty) as sheet:
            assert sheet.size[0] > 0
