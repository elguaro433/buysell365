"""Flask application factory para Web Admin Panel."""
from flask import Flask
from . import config


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB max upload

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.whatsapp import whatsapp_bp
    from .routes.vip import vip_bp
    from .routes.logs import logs_bp
    from .routes.signals import signals_bp
    from .routes.config_route import config_bp
    from .routes.control import control_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(whatsapp_bp, url_prefix="/whatsapp")
    app.register_blueprint(vip_bp, url_prefix="/vip")
    app.register_blueprint(logs_bp, url_prefix="/logs")
    app.register_blueprint(signals_bp, url_prefix="/signals")
    app.register_blueprint(config_bp, url_prefix="/config")
    app.register_blueprint(control_bp, url_prefix="/control")

    # Filter para fechas
    @app.template_filter("ts")
    def ts_filter(value):
        import time
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
        except Exception:
            return value or "-"

    return app
