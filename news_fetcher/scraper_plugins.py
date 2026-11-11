"""
Pluggable scraper fallback system for MuckScraper.

Allows optional external fallback handlers (e.g. web archives, institutional
library proxies, custom unpaywall services) to attempt article recovery when
direct publisher scraping fails or encounters access gates.

To register a plugin:
  1. Built-in unpaywall & archive fallback:
     ENABLE_UNPAYWALL_FALLBACK=true
  2. Custom plugin environment variable:
     SCRAPER_FALLBACK_PLUGINS="module.path:handler_function,another.module:handler"
  3. Programmatic registration:
     from news_fetcher.scraper_plugins import register_fallback_plugin
     register_fallback_plugin("my_plugin", my_handler_fn)

Handler signature:
  handler(url: str, initial_html: str | None = None) -> tuple[str | None, str | None]
  Must return (sanitized_html, method_name) on success, or (None, None).
"""

from typing import Callable, Optional, Tuple, List
import logging
import importlib
import os

logger = logging.getLogger(__name__)

FallbackHandler = Callable[[str, Optional[str]], Tuple[Optional[str], Optional[str]]]

_REGISTERED_PLUGINS: List[Tuple[str, FallbackHandler]] = []
_INITIALIZED = False


def register_fallback_plugin(name: str, handler: FallbackHandler) -> None:
    """Register a custom scraper fallback plugin handler."""
    global _REGISTERED_PLUGINS
    # Avoid duplicate registrations
    for existing_name, _ in _REGISTERED_PLUGINS:
        if existing_name == name:
            return
    _REGISTERED_PLUGINS.append((name, handler))
    logger.info(f"[ScraperPlugins] Registered fallback plugin: {name}")


def clear_fallback_plugins() -> None:
    """Clear all registered fallback plugins (useful in tests)."""
    global _REGISTERED_PLUGINS, _INITIALIZED
    _REGISTERED_PLUGINS = []
    _INITIALIZED = False


def _init_plugins_from_env() -> None:
    """Discover and load fallback plugins configured in the environment."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    # Check environment variable
    plugin_specs = os.environ.get("SCRAPER_FALLBACK_PLUGINS", "").strip()
    if plugin_specs:
        for spec in plugin_specs.split(","):
            spec = spec.strip()
            if not spec:
                continue
            try:
                if ":" in spec:
                    mod_path, func_name = spec.split(":", 1)
                else:
                    mod_path, func_name = spec, "unpaywall_article"
                mod = importlib.import_module(mod_path)
                handler = getattr(mod, func_name)
                register_fallback_plugin(mod_path.split(".")[-1], handler)
            except Exception as e:
                logger.warning(f"[ScraperPlugins] Failed to load plugin '{spec}': {e}")
        return

    # Auto-register local unpaywall plugin if explicitly enabled
    enable_unpaywall = os.environ.get("ENABLE_UNPAYWALL_FALLBACK", "false").lower() in ("1", "true", "yes")
    if enable_unpaywall:
        try:
            from news_fetcher.unpaywall import unpaywall_article
            register_fallback_plugin("unpaywall", unpaywall_article)
        except ImportError:
            pass


def get_fallback_plugins() -> List[Tuple[str, FallbackHandler]]:
    """Return all registered fallback plugins, initializing from env if needed."""
    _init_plugins_from_env()
    return list(_REGISTERED_PLUGINS)


def run_fallback_plugins(url: str, initial_html: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Execute registered fallback plugins in order until one returns content.
    Returns (sanitized_html, method_label) or (None, None).
    """
    plugins = get_fallback_plugins()
    for name, handler in plugins:
        try:
            content, method = handler(url, initial_html=initial_html)
            if content:
                label = f"{name}_{method}" if method else name
                return content, label
        except Exception as e:
            logger.debug(f"[ScraperPlugins] Plugin '{name}' error on {url[:60]}: {e}")
    return None, None
