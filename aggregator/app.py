# aggregator/app.py
from aggregator import create_app, create_db

app = create_app()

if __name__ == "__main__":
    create_db(app)
    app.run(host="0.0.0.0", port=5000, debug=True)