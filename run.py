"""
Clinical Trial Scheduler application entry point.

Development:
  python run.py

Production (recommended on Windows):
  set FLASK_DEBUG=0
  python run.py
"""
import os

from app import app


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(base_dir, "uploads"), exist_ok=True)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = _as_bool(os.getenv("FLASK_DEBUG", "0"), default=False)

    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        try:
            from waitress import serve

            serve(app, host=host, port=port, threads=8)
        except Exception:
            # Fallback keeps the app runnable if waitress is unavailable.
            app.run(host=host, port=port, debug=False)
