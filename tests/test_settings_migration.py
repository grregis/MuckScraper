"""The extracted migration must follow a real revision in this branch."""
from pathlib import Path
from alembic.script import ScriptDirectory


def test_saved_settings_migration_is_the_single_head():
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory(str(root/'migrations'))
    assert script.get_heads() == ['d3b8f6a1e2c9']
    assert script.get_revision('d3b8f6a1e2c9').down_revision == 'a9c4e2f7b1d5'
