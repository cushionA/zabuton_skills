import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from check_deck import cmd_actions, cmd_boxes


class BoxChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.truth = {
            "data.png": {"scale": 1.5, "boxes": {
                f"data:{i}": [i * 100, 20, 40, 20] for i in range(1, 6)
            }},
            "ops.png": {"scale": 1.5, "boxes": {"op:open": [100, 40, 40, 20]}},
            "member.png": {"scale": 1.25, "boxes": {
                "data:3": [100, 20, 40, 20], "data:13": [200, 20, 40, 20]
            }},
        }
        self.truth_path = self.root / "truth.json"
        self.truth_path.write_text(json.dumps(self.truth), encoding="utf-8")

    def shot(self, image="data.png", items=None):
        reference = self.truth[image]
        keys = list(reference["boxes"]) if items is None else [f"data:{i}" for i in items]
        return {"image": image, "scale": reference["scale"], "steps": [
            {"kind": "data", "item": int(key.split(":")[1]), "box": reference["boxes"][key].copy()}
            if key.startswith("data:") else {"kind": "op", "box": reference["boxes"][key].copy()}
            for key in keys
        ]}

    def check(self, shots, **overrides):
        scenario = self.root / "scenario.json"
        scenario.write_text(json.dumps({"shots": shots}), encoding="utf-8")
        values = dict(scenario=str(scenario), truth=str(self.truth_path), images=["data.png"],
                      min_iou=0.5, max_offset=12.0, min_coverage=1.0, require_box=[])
        values.update(overrides)
        with contextlib.redirect_stdout(io.StringIO()):
            return cmd_boxes(Namespace(**values))

    def test_complete_selected_image_does_not_require_other_truth_images(self):
        self.assertEqual(self.check([self.shot()]), 0)

    def test_empty_shots_steps_and_unknown_images_fail(self):
        for shots in ([], [self.shot(items=[])], [{"image": "unknown.png", "steps": []}]):
            with self.subTest(shots=shots):
                self.assertEqual(self.check(shots), 1)

    def test_mark_and_unscored_boxes_do_not_count(self):
        for step in ({"kind": "mark", "box": [100, 20, 40, 20]},
                     {"kind": "data", "item": 99, "box": [100, 20, 40, 20]}):
            shot = self.shot(items=[])
            shot["steps"] = [step]
            with self.subTest(step=step):
                self.assertEqual(self.check([shot]), 1)

    def test_required_image_must_have_a_matching_box(self):
        self.assertEqual(self.check([self.shot()], images=["data.png", "ops.png"]), 1)
        self.assertEqual(self.check([self.shot(), self.shot("ops.png", [])],
                                    images=["data.png", "ops.png"]), 1)
        self.assertEqual(self.check([self.shot(), self.shot("ops.png")],
                                    images=["data.png", "ops.png"]), 0)

    def test_coverage_boundary_counts_correct_distinct_items(self):
        self.assertEqual(self.check([self.shot(items=[1, 2, 3, 4])], min_coverage=0.8), 0)
        self.assertEqual(self.check([self.shot(items=[1, 2, 3])], min_coverage=0.8), 1)
        self.assertEqual(self.check([self.shot(items=[1, 2, 3, 4])]), 1)
        shot = self.shot()
        shot["steps"][-1]["box"][1] += 1000
        self.assertEqual(self.check([shot], min_coverage=0.8), 0)

    def test_duplicate_shots_do_not_inflate_coverage(self):
        shot = self.shot(items=[1, 2, 3])
        self.assertEqual(self.check([shot, shot], min_coverage=0.8), 1)
        self.assertEqual(self.check([self.shot(items=[1, 2]), self.shot(items=[3, 4, 5])]), 0)

    def test_member_price_is_required_even_if_coverage_passes(self):
        options = dict(images=["member.png"], min_coverage=0.5,
                       require_box=[["member.png", "data:13"]])
        self.assertEqual(self.check([self.shot("member.png", [3])], **options), 1)
        self.assertEqual(self.check([self.shot("member.png", [13])], **options), 0)
        shot = self.shot("member.png")
        shot["steps"][1]["box"][1] += 1000
        self.assertEqual(self.check([shot], **options), 1)

    def test_iou_boundary(self):
        shot = self.shot(items=[1])
        shot["steps"][0]["box"][2] *= 2
        self.assertEqual(self.check([shot], min_coverage=0.2, max_offset=0), 0)
        shot["steps"][0]["box"][2] += 0.01
        self.assertEqual(self.check([shot], min_coverage=0.2, max_offset=0), 1)

    def test_offset_boundary_uses_css_pixels(self):
        shot = self.shot(items=[1])
        shot["steps"][0]["box"][1] += 18
        self.assertEqual(self.check([shot], min_coverage=0.2, min_iou=1), 0)
        shot["steps"][0]["box"][1] += 0.01
        self.assertEqual(self.check([shot], min_coverage=0.2, min_iou=1), 1)

    def test_invalid_thresholds_fail(self):
        for name, invalid in (("min_iou", [0, -0.1, 1.1, float("nan"), float("inf")]),
                              ("min_coverage", [0, -0.1, 1.1, float("nan"), float("inf")]),
                              ("max_offset", [-1, float("nan"), float("inf")])):
            for value in invalid:
                with self.subTest(name=name, value=value):
                    self.assertEqual(self.check([self.shot()], **{name: value}), 1)

    def test_invalid_image_and_required_box_ids_fail(self):
        for options in (dict(images=[]), dict(images=["missing.png"]),
                        dict(require_box=[["data.png", "data:99"]]),
                        dict(require_box=[["member.png", "data:13"]])):
            with self.subTest(options=options):
                self.assertEqual(self.check([self.shot()], **options), 1)

    def test_invalid_item_ids_fail(self):
        for item in (0, -1, 1.0, True, "", "x", "01"):
            shot = self.shot()
            shot["steps"][0]["item"] = item
            with self.subTest(item=item):
                self.assertEqual(self.check([shot], min_coverage=0.8), 1)

    def test_invalid_rectangles_fail(self):
        for box in ([0, 0, 0, 20], [0, 0, 20, -1], [0, 0, 20], [0, float("nan"), 20, 20]):
            shot = self.shot()
            shot["steps"][0]["box"] = box
            with self.subTest(box=box):
                self.assertEqual(self.check([shot], min_coverage=0.8), 1)

    def test_wrong_scale_fails(self):
        shot = self.shot()
        shot["scale"] = 1
        self.assertEqual(self.check([shot]), 1)


class ActionChecksTest(unittest.TestCase):
    def test_type_shorthand_and_internal_form_detect_password_input(self):
        with tempfile.TemporaryDirectory() as temp:
            scenario = Path(temp) / "scenario.json"
            for action in ({"type": "abc"}, {"type": "click"}, {"type": "type", "value": "abc"}):
                scenario.write_text(json.dumps({"shots": [{"steps": [
                    {"action": action, "target": {"label": "パスワード"}}
                ]}]}), encoding="utf-8")
                with self.subTest(action=action), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cmd_actions(Namespace(files=[str(scenario)])), 1)

    def test_non_input_action_does_not_flag_password_target(self):
        with tempfile.TemporaryDirectory() as temp:
            scenario = Path(temp) / "scenario.json"
            scenario.write_text(json.dumps({"shots": [{"steps": [
                {"action": "click", "target": {"label": "パスワード"}}
            ]}]}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cmd_actions(Namespace(files=[str(scenario)])), 0)


if __name__ == "__main__":
    unittest.main()
