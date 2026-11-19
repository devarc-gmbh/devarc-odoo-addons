import pathlib

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE_ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_SNIPPETS = (
    "import anthro" + "pic",
    "api.anthro" + "pic.com",
    "import open" + "ai",
    "ANTHROPIC_API" + "_KEY",
    "OPENAI_API" + "_KEY",
)


@tagged("post_install", "-at_install", "ai_assistant_access")
class TestNoAiCredentials(TransactionCase):
    def test_module_never_references_ai_provider_credentials(self):
        sources = (
            list(MODULE_ROOT.rglob("*.py"))
            + list(MODULE_ROOT.rglob("*.xml"))
            + list((MODULE_ROOT / "static" / "tools").rglob("*.md"))
        )
        self.assertTrue(any(path.suffix == ".md" for path in sources))
        self.assertTrue(sources)
        for path in sources:
            content = path.read_text(encoding="utf-8")
            for snippet in FORBIDDEN_SNIPPETS:
                self.assertNotIn(snippet, content, f"{path.name}: {snippet}")
