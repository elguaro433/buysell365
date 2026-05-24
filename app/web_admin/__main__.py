"""Entry point: python -m web_admin

Arranca el panel web usando waitress (WSGI production-ready).
"""
import sys
import os

# Asegurar que el padre (app/) está en path para que el bot importe correctamente
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cargar .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from web_admin.app import create_app
from web_admin import config


def main():
    app = create_app()
    print(f"=" * 60)
    print(f"  BuySell365 — Web Admin Panel")
    print(f"=" * 60)
    print(f"  Host:     {config.WEB_ADMIN_HOST}")
    print(f"  Port:     {config.WEB_ADMIN_PORT}")
    print(f"  User:     {config.WEB_ADMIN_USER}")
    print(f"  URL:      http://localhost:{config.WEB_ADMIN_PORT}")
    print(f"=" * 60)
    print(f"  (Ctrl+C para parar)")
    print()

    try:
        from waitress import serve
        serve(app, host=config.WEB_ADMIN_HOST, port=config.WEB_ADMIN_PORT, threads=8)
    except ImportError:
        # Fallback dev server
        app.run(host=config.WEB_ADMIN_HOST, port=config.WEB_ADMIN_PORT, debug=False)


if __name__ == "__main__":
    main()
