"""Settings › Source API keys: keys for the news APIs ingestion can use
(NewsAPI, GNews, the New York Times' Article Search). Saved like the LLM
provider keys (aggregator/config_store.py): encrypted, write-only, and
overriding the environment variable of the same name. Ingestion reads them
through news_fetcher/saved_settings.py."""
from flask import flash, redirect, render_template, url_for

from aggregator import config_store, secret_box
from aggregator.permissions import ROLE_ADMIN, role_required

from . import admin
from .llm_settings import save_fields_form

# key: (name, what it adds, where to get a key)
SOURCES = {
    "newsapi": ("NewsAPI", "Top headlines and keyword search for scheduled fetches.", "https://newsapi.org/register"),
    "gnews": ("GNews", "Top headlines and keyword search for scheduled fetches.", "https://gnews.io/register"),
    "nyt": ("New York Times",
            "NYT articles are paywalled, so the scraper only gets their RSS description. With a free "
            "Article Search API key, a NYT article gets its abstract and opening paragraph instead. "
            "The key allows 500 requests a day; the limit keeps MuckScraper under that.",
            "https://developer.nytimes.com/get-started"),
}


@admin.route("/settings/source-keys")
@role_required(ROLE_ADMIN)
def source_keys_page():
    states = config_store.field_states()
    sources = []
    for key, (name, about, signup) in SOURCES.items():
        if key not in config_store.SOURCE_FIELDS:
            continue
        fields = [
            {"name": n, "label": label, "kind": kind, **states[n]}
            for n, label, kind in config_store.SOURCE_FIELDS[key]
        ]
        sources.append({
            "key": key, "name": name, "about": about, "signup": signup, "fields": fields,
            "configured": any(f["kind"] == "secret" and f["source"] != "unset" for f in fields),
        })
    return render_template("settings_source_keys.html", sources=sources, key_source=secret_box.key_source())


@admin.route("/settings/source-keys/<key>", methods=["POST"])
@role_required(ROLE_ADMIN)
def save_source_keys(key):
    fields = config_store.SOURCE_FIELDS.get(key)
    if fields is None:
        flash("Unknown source.", "error")
    elif save_fields_form(fields):
        flash(f"{SOURCES[key][0]} settings saved.", "success")
    return redirect(url_for("admin.source_keys_page") + f"#source-{key}")
