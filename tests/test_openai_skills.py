"""D6 declared-inventory tests; fixture files never enter production logs."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from tools import gen_openai_skills as gen

ROOT = Path(__file__).resolve().parent.parent


class TestOpenAISkills(unittest.TestCase):
    def test_committed_declared_inventory_is_current(self):
        self.assertEqual(gen.INVENTORY, ("ship",))
        self.assertTrue(gen.generate(ROOT, check=True))

    def test_router_has_canonical_reference_and_mapping_without_copied_body(self):
        text = gen.render(ROOT, "ship")
        self.assertIn("../../../.claude/skills/ship/SKILL.md", text)
        self.assertIn("../../../docs/openai-workflow.md", text)
        self.assertIn("qa-verify and prd-close", text)
        self.assertIn("complete discovery pending", text)
        self.assertNotIn("### QD1.", text)

    def test_generation_drift_metadata_extra_and_missing_source(self):
        with tempfile.TemporaryDirectory(prefix="openai-skills-") as tmp:
            root = Path(tmp)
            source = root / ".claude/skills/ship/SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_bytes((ROOT / ".claude/skills/ship/SKILL.md").read_bytes())
            with redirect_stdout(io.StringIO()):
                self.assertFalse(gen.generate(root, check=True))
                self.assertFalse((root / ".agents").exists())
                self.assertTrue(gen.generate(root))
                target = root / ".agents/skills/ship/SKILL.md"
                first = target.read_bytes()
                self.assertTrue(gen.generate(root))
                self.assertEqual(first, target.read_bytes())
                self.assertTrue(gen.generate(root, check=True))
                target.write_bytes(first.replace(b"\n", b"\r\n"))
                crlf = target.read_bytes()
                self.assertTrue(gen.generate(root, check=True))
                self.assertEqual(crlf, target.read_bytes())
                target.write_bytes(first)
                source.write_text(source.read_text(encoding="utf-8").replace(
                    "description:", "description: Updated "), encoding="utf-8")
                self.assertFalse(gen.generate(root, check=True))
                self.assertEqual(first, target.read_bytes())
                gen.generate(root)
                extra = root / ".agents/skills/extra/SKILL.md"
                extra.parent.mkdir()
                extra.write_text("extra", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unexpected routers"):
                    gen.generate(root, check=True)
                extra.unlink()
                source.unlink()
                with self.assertRaises(FileNotFoundError):
                    gen.generate(root, check=True)

    def test_new_canonical_skill_is_outside_explicit_d6_subset(self):
        with tempfile.TemporaryDirectory(prefix="openai-skills-") as tmp:
            root = Path(tmp)
            for name in ("ship", "future"):
                path = root / ".claude/skills" / name / "SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text(f"---\nname: {name}\ndescription: example\n---\n",
                                encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                gen.generate(root)
                self.assertTrue(gen.generate(root, check=True))
            self.assertIn("ship only", out.getvalue())
            self.assertFalse((root / ".agents/skills/future").exists())

    def test_bad_discovery_metadata_refuses(self):
        with tempfile.TemporaryDirectory(prefix="openai-skills-") as tmp:
            root = Path(tmp)
            path = root / ".claude/skills/ship/SKILL.md"
            path.parent.mkdir(parents=True)
            for text in ("body", "---\nname: wrong\ndescription: x\n---\n",
                         "---\nname: ship\n---\n",
                         "---\nname: ship\nname: ship\ndescription: x\n---\n"):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(ValueError):
                    gen.render(root, "ship")


if __name__ == "__main__":
    unittest.main()
