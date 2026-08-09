import os

from Silento_app import app

application = app

if __name__ == "__main__":
    application.run(
        host = "0.0.0.0",
        port = int(os.environ.get("PORT", "5000")),
        debug = False
    )