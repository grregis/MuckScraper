"""Valid table forms must retain existing field and CSRF serialization."""
from pathlib import Path
from types import SimpleNamespace as Row

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name, form_id, expected", [
    ("topics", "topic-7", {"icon": "N", "sort_order": "3", "is_active": "on"}),
    ("rss_feeds", "rss-feed-7", {"fallback_url": "https://example.org/fallback", "label": "Example", "enabled": "on"}),
    ("pipeline_schedule", "pipeline-7", {"hour": "9", "run_full_pipeline": "on", "is_active": "on"}),
])
def test_row_form_owns_its_controls(name, form_id, expected):
    playwright = pytest.importorskip("playwright.sync_api")
    row = Row(id=7, name="Example", icon="N", sort_order=3, is_active=True,
              url="https://example.org/feed", fallback_url="https://example.org/fallback",
              label="Example", enabled=True, hour=9, run_full_pipeline=True)
    env = Environment(loader=FileSystemLoader(ROOT / "aggregator/templates"))
    env.globals.update(url_for=lambda endpoint, **kw: "/" + endpoint,
                       settings_sections=lambda active=None: [], csrf_token=lambda: "fixture-csrf", can=lambda role: True,
                       menu_back=lambda: "/headlines",
                       current_user=Row(is_authenticated=True, username="Example", role_label="Admin"))
    html = env.get_template(name + ".html").render(
        topics=[row], entries=[row], buckets=["general"],
        feeds_by_bucket={"general": [row]}, bucket_labels={"general": "General"})
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(3000)
        page.set_content(html)
        save = page.locator(".save-btn").first
        fields = save.evaluate("button => Object.fromEntries(new FormData(button.form))")
        assert fields == {**expected, "csrf_token": "fixture-csrf"}
        # A form may live inside a table cell, not directly under a table row.
        assert save.evaluate("button => button.form.closest('td') !== null")
        assert save.evaluate("button => button.form.id") == form_id
        # Delete remains a separate CSRF-protected action, never nested in Save.
        for form in page.locator("form[action*='delete']").all():
            assert form.evaluate("form => [...new FormData(form).keys()]") == ["csrf_token"]
        browser.close()
