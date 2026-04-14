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

def init_db():
    with app.app_context():
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.session.commit()
        db.create_all()

if __name__ == "__main__":
    init_db()
    # Run on all interfaces so it's accessible from outside the container
    app.run(host="0.0.0.0", port=5000, debug=True)
