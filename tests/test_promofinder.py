"""Run: python -m unittest discover -s tests -v   (needs no input pack: uses a synthetic screenplay)"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder.parse import parse_episode, load_show, to_sec  # noqa: E402
from promofinder.pipeline import build  # noqa: E402
from promofinder.llm import parse_json  # noqa: E402

LINES = [
    ("CAMERA", None, "Wide shot of a ballroom."),
    ("MAID (Nervous)", None, "My lady, the guests are waiting."),
    ("LADY ROSE (Cold)", None, "Let them wait."),
    ("ACTION", None, "Lord Hale storms in and slaps the maid across the face."),
    ("SOUND FX", None, "A sharp slap echoes through the hall."),
    ("LORD HALE (Furious)", None, "You lied to me! You knew she was alive!"),
    ("LADY ROSE (Shocked)", None, "Who told you that?"),
    ("LORD HALE (Menacing)", None, "Your own sister. She is downstairs."),
    ("LADY ROSE (Defiant)", None, "Then she is a liar."),
    ("LORD HALE", None, "We will see at the wedding."),
    ("ACTION", None, "Rose grips the table, her knuckles white."),
    ("LADY ROSE (Whispering)", None, "If she speaks, I am ruined."),
    ("LORD HALE (Cold)", None, "Then you had better pray she stays silent."),
    ("SOUND FX", None, "Heavy footsteps on the stairs; a door bursts open."),
    ("SISTER (O.S.) (Triumphant)", None, "Hello, Rose. Did you miss me?"),
    ("ACTION", None, "Rose freezes, all colour gone from her face, as the screen cuts to black on the cliffhanger."),
]


def write_ep(path, n, crlf=False, hhmmss=False, no_scene=False):
    t = 0.0
    out = [f"EPISODE NUMBER: Episode {n}", "EPISODE LOGLINE: A secret sister returns on the wedding day.",
           "CONTINUITY NOTES: Picks up directly from the previous episode.", "", "--- SCRIPT START ---", ""]
    if not no_scene:
        out += ["SCENE 01", "Scene heading: INT. BALLROOM - NIGHT", "Scene start: 00:00.00", "Scene end: 01:10.00", ""]
    for label, _, text in LINES * 2:
        dur = 3.4
        f = (lambda x: f"00:{int(x//60):02d}:{x%60:05.2f}") if hhmmss else (lambda x: f"{int(x//60):02d}:{x%60:05.2f}")
        out += [f"**{label}:**", f"Start: ({f(t)}) | End: ({f(t+dur)})", f'"{text}"' if label[0] not in "CAS" else text, ""]
        t += dur + 0.4
    out += ["--- SCRIPT END ---"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(("\r\n" if crlf else "\n").join(out))


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for n in range(1, 5):
            write_ep(os.path.join(self.d, f"episode-{n:03d}.txt"), n, crlf=(n == 2), hhmmss=(n == 3), no_scene=(n == 4))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_timestamps(self):
        self.assertAlmostEqual(to_sec("01:02.50"), 62.5)
        self.assertAlmostEqual(to_sec("00:01:02.50"), 62.5)

    def test_parse_variants(self):
        for n in range(1, 5):
            ep = parse_episode(os.path.join(self.d, f"episode-{n:03d}.txt"))
            self.assertEqual(ep.number, n)
            self.assertGreater(len(ep.beats), 25)
            self.assertTrue(any(b.offscreen for b in ep.beats))
            self.assertGreater(ep.duration, 100)

    def test_pipeline_constraints(self):
        res = build(self.d, title="Secret Sister", log=lambda *a: None)
        self.assertTrue(res["promos"])
        for c in res["promos"]:
            self.assertGreaterEqual(c.duration, 20 - 0.01)
            self.assertLessEqual(c.duration, 60 + 0.01)
            for a, b in zip(c.segments, c.segments[1:]):
                self.assertLessEqual((a.episode, a.end), (b.episode, b.start + 0.01))
            ix = res["index"][c.segments[0].episode]
            self.assertLessEqual(c.hook_unit.start - c.segments[0].start, 6.0)
            for s in c.segments:
                self.assertIsNone(res["index"][s.episode].line_at(s.start))
                self.assertIsNone(res["index"][s.episode].line_at(s.end))

    def test_zip_input(self):
        z = os.path.join(self.d, "show.zip")
        with zipfile.ZipFile(z, "w") as zf:
            for n in range(1, 5):
                zf.write(os.path.join(self.d, f"episode-{n:03d}.txt"), f"show/episode-{n:03d}.txt")
        self.assertEqual(len(load_show(z)), 4)

    def test_json_recovery(self):
        self.assertEqual(parse_json('Sure!\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('blah {"a": [1, 2]} trailing'), {"a": [1, 2]})


if __name__ == "__main__":
    unittest.main()
