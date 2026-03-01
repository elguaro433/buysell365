import os

# Bind to PORT env var (Render sets this to 10000)
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Single worker to save memory on free tier
workers = 1
threads = 2

# Timeout settings
timeout = 120
graceful_timeout = 30

# Keep alive
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
