# muckscraperHeadlinesGoogleNEW/aggregator/app.py
import logging
from aggregator import create_app, db
from flask_migrate import Migrate

# Filter out HAProxy health checks from logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return "OPTIONS / HTTP/1.0\" 200" not in record.getMessage()

logging.getLogger("werkzeug").addFilter(HealthCheckFilter())

app = create_app()

# On startup, clear any background-task statuses stuck 'running' well past a
# plausible runtime -- almost certainly a leftover from a process that died
# mid-task (restart, crash, OOM), which would otherwise block that action from
# ever being re-run. Safe to run at every worker's boot with GUNICORN_WORKERS
# > 1: reconcile_orphaned_task_statuses() only touches stale rows, so it can't
# clobber a task that's legitimately still running in a different worker.
# Guarded so a reconciliation failure can never prevent the app from booting.
with app.app_context():
    try:
        from aggregator.blueprints.admin import reconcile_orphaned_task_statuses
        reconcile_orphaned_task_statuses()
    except Exception:
        logging.getLogger(__name__).exception(
            "Startup task-status reconciliation failed"
        )

def init_db():
    with app.app_context():
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.session.commit()
        db.create_all()

if __name__ == "__main__":
    init_db()
    # Run on all interfaces so it's accessible from outside the container
    app.run(host="0.0.0.0", port=5000, debug=True)
