import ast
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_PATHS = {
    "aggregator/public_app.py",
    "aggregator/blueprints/personal.py",
    "aggregator/templates/headlines.html",
    "aggregator/templates/public_article.html",
    "aggregator/templates/public_story.html",
    "aggregator/templates/archive.html",
    "aggregator/templates/about.html",
    "aggregator/templates/404.html",
    "aggregator/templates/500.html",
    "news_fetcher/headline_ranker.py",
    "news_fetcher/editorial_ranker.py",
    "private_site/export_static.py",
    "docker-compose.private.yml",
}


def git_ls_files():
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise unittest.SkipTest("git executable is not available") from exc
    return set(result.stdout.splitlines())


class OpenSourceBoundaryTests(unittest.TestCase):
    def test_private_files_are_not_tracked(self):
        tracked = git_ls_files()
        leaked = sorted(PRIVATE_PATHS & tracked)
        self.assertEqual(leaked, [])

    def test_open_source_app_factory_does_not_import_private_blueprint(self):
        source = (ROOT / "aggregator" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("create_public_app", source)
        self.assertNotIn("blueprints.personal", source)
        self.assertNotIn("app.register_blueprint(personal", source)

    def test_base_compose_does_not_define_public_site(self):
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?m)^  public:\s*$")
        self.assertNotIn("5001:5000", source)
        self.assertNotIn("aggregator.public_app", source)

    def test_tracked_files_do_not_reference_private_routes_or_public_app(self):
        tracked = git_ls_files()
        offenders = {}
        forbidden = (
            "aggregator.public_app",
            "blueprints.personal",
            "url_for('personal.",
            'url_for("personal.',
            "personal.",
        )

        for rel_path in tracked:
            path = ROOT / rel_path
            if not path.is_file():
                continue
            if rel_path == ".gitignore" or rel_path.startswith("tests/"):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            hits = [token for token in forbidden if token in source]
            if hits:
                offenders[rel_path] = hits

        self.assertEqual(offenders, {})

    def test_scheduler_private_hooks_are_importerror_guarded(self):
        tree = ast.parse((ROOT / "news_fetcher" / "scheduler.py").read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

        expected = {
            "run_optional_headline_ranking": "news_fetcher.headline_ranker",
            "run_optional_static_export": "private_site.export_static",
        }

        for function_name, module_name in expected.items():
            with self.subTest(function=function_name):
                function = functions.get(function_name)
                self.assertIsNotNone(function)
                guarded = False
                for node in ast.walk(function):
                    if not isinstance(node, ast.Try):
                        continue
                    imports_module = any(
                        isinstance(child, ast.ImportFrom) and child.module == module_name
                        for child in ast.walk(node)
                    )
                    catches_import_error = any(
                        isinstance(handler.type, ast.Name)
                        and handler.type.id == "ImportError"
                        for handler in node.handlers
                    )
                    if imports_module and catches_import_error:
                        guarded = True
                        break
                self.assertTrue(guarded, f"{module_name} import is not ImportError-guarded")

    def test_public_get_routes_do_not_generate_or_commit_llm_content(self):
        source = (ROOT / "aggregator" / "blueprints" / "public.py").read_text(encoding="utf-8")
        self.assertNotIn("news_fetcher.summarizer", source)
        self.assertNotIn("summarize_story", source)
        self.assertNotIn("summarize_article", source)
        self.assertNotIn("generate_deep_report", source)
        self.assertNotIn("db.session.commit", source)

    def test_export_static_does_not_redefine_apply_aggregator_filter(self):
        # Regression guard for the 2026-09-09 Fable audit finding: export_static.py
        # carried its own copy of apply_aggregator_filter() that never got the
        # is_independent_source() corroboration floor added in 317c108, so the
        # published muckscraper.news site overstated outlet counts on 7 of 20
        # stories in one audited edition while the admin app (which uses the
        # canonical aggregator.story_view version) showed the correct numbers.
        # Fixed by deleting the duplicate and importing the canonical function --
        # this asserts the duplicate doesn't quietly come back.
        export_static_path = ROOT / "private_site" / "export_static.py"
        if not export_static_path.exists():
            self.skipTest("private_site/ is not present on this checkout (main)")
        tree = ast.parse(export_static_path.read_text(encoding="utf-8"))
        defined_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("apply_aggregator_filter", defined_names)

        imports_canonical = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "aggregator.story_view"
            and any(alias.name == "apply_aggregator_filter" for alias in node.names)
            for node in tree.body
        )
        self.assertTrue(imports_canonical, "export_static.py must import apply_aggregator_filter from aggregator.story_view")

    def test_app_factory_builds_without_private_routes_when_dependencies_exist(self):
        try:
            from aggregator import create_app
        except ModuleNotFoundError as exc:
            self.skipTest(f"Project dependencies are not installed: {exc}")

        app = create_app()
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

        self.assertIn("public.index", endpoints)
        self.assertIn("admin.list_articles", endpoints)
        self.assertIn("auth.login", endpoints)
        self.assertFalse(any(endpoint.startswith("personal.") for endpoint in endpoints))


if __name__ == "__main__":
    unittest.main()
