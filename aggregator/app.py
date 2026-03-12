# aggregator/app.py
import logging
from aggregator import create_app, create_db

app = create_app()

if __name__ == "__main__":
    create_db(app)
    log = logging.getLogger('werkzeug')
    log.addFilter(lambda r: 'OPTIONS' not in r.getMessage())
    app.run(host="0.0.0.0", port=5000, debug=True)