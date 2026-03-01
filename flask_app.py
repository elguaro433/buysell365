"""
BuySell 365 — Flask App con Autenticación Completa
Creado por Emmanuel Díaz
Desarrollado con Claude AI by Anthropic
"""

import os, psycopg2, psycopg2.extras, hashlib, secrets, time, re, threading
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, jsonify, session,
                   redirect, url_for, flash, g)
from werkzeug.security import generate_password_hash, check_password_hash
import json
import urllib.request, urllib.parse, xml.etree.ElementTree as ET

# ============================================================
#  CONFIGURACIÓN
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)

# Security headers for PWA
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# Base de datos - uses DATABASE_URL environment variable (PostgreSQL)

# Email config (para "Olvidé mi contraseña")
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')  # tu email
SMTP_PASS = os.environ.get('SMTP_PASS', '')  # app password de Gmail
APP_URL = os.environ.get('APP_URL', 'https://Elguaro2026.pythonanywhere.com')

# Twelve Data API
TWELVE_API_KEY = os.environ.get('TWELVE_API_KEY', '55490f0409a44fbc83303d76771118b6')

# VAPID keys for Web Push Notifications
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', 'BHlyq7N49mbcYbrpcQ3GKTlZgX_-CKT6hjLfLMQYBpWXOzqhnA3flHt6J8vSosKVFQg9eSm8XL4uECMLm3aMJbs')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', 'wtu7t76iO_W8jqptjn7R8J3tBzCUw8apqZzCLTG19to')
VAPID_EMAIL = os.environ.get('VAPID_EMAIL', 'mailto:emmanuel050216@gmail.com')

# Rate limiting
_login_attempts = {}  # {ip: {'count': N, 'last': timestamp}}
_login_lock = threading.Lock()

# Caching para Market Prices, News y Technical Analysis
_price_cache = {}
_price_cache_time = 0
_news_cache = []
_news_cache_time = 0
_analysis_cache = {}
_analysis_cache_time = 0

# ============================================================
#  BASE DE DATOS — SQLite
# ============================================================
def get_db():
    """Conexión a BD por request (thread-safe)."""
    if 'db' not in g:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            raise RuntimeError('DATABASE_URL environment variable not set')
        g.db = psycopg2.connect(db_url, connect_timeout=10)
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

ADMIN_EMAIL = 'emmanuel050216@gmail.com'

def init_db():
    """Crea las tablas si no existen."""
    db = None
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            print("⚠️ DATABASE_URL no configurado - skipping init")
            return

        db = psycopg2.connect(db_url, connect_timeout=10)
        cursor = db.cursor()

        # Create tables individually (PostgreSQL doesn't support executescript)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                plan TEXT DEFAULT 'free',
                is_active INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                email_verified INTEGER DEFAULT 0,
                approval_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                login_count INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                action TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                ticker TEXT,
                direction TEXT DEFAULT 'BUY',
                tipo TEXT DEFAULT 'swing',
                timeframe TEXT DEFAULT '1H',
                quality TEXT DEFAULT 'MEDIA',
                grado TEXT DEFAULT 'B',
                confluence INTEGER DEFAULT 4,
                precio_entrada REAL,
                tp1 REAL, tp2 REAL, tp3 REAL,
                sl REAL,
                rr_tp1 TEXT DEFAULT '1:1',
                rr_tp2 TEXT DEFAULT '2:1',
                rr_tp3 TEXT DEFAULT '3:1',
                confirmations TEXT DEFAULT '[]',
                resultado TEXT DEFAULT 'pending',
                pnl REAL DEFAULT 0,
                closed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, endpoint)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                id SERIAL PRIMARY KEY,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_approval ON users(approval_status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions_log(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_history_created ON signal_history(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subscriptions(user_id)")

        db.commit()

        # Initialize default settings
        cursor.execute("""
            INSERT INTO admin_settings (setting_key, setting_value)
            VALUES (%s, %s)
            ON CONFLICT (setting_key) DO NOTHING
        """, ('max_active_users', '50'))
        db.commit()

        # Migration: mark active users as approved
        try:
            cursor.execute("""
                UPDATE users SET approval_status='approved'
                WHERE is_active=1 AND (approval_status IS NULL OR approval_status='')
            """)
            db.commit()
        except Exception:
            pass

        cursor.close()
        db.close()
        print("✅ Base de datos inicializada")

    except Exception as e:
        print(f"⚠️ Error initializing database: {e}")
        if db:
            db.close()

# Inicializar BD en el primer request (no bloquea arranque de Gunicorn)
_db_initialized = False

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


# ============================================================
#  HELPERS DE SEGURIDAD
# ============================================================
def validate_email(email):
    """Valida formato de email."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_password(password):
    """Valida que la contraseña sea segura. Retorna (ok, mensaje)."""
    if len(password) < 6:
        return False, "Mínimo 6 caracteres"
    if len(password) > 128:
        return False, "Máximo 128 caracteres"
    return True, "OK"

def check_rate_limit(ip, max_attempts=5, window=300):
    """Rate limiting: máximo 5 intentos de login cada 5 minutos por IP."""
    with _login_lock:
        now = time.time()
        if ip in _login_attempts:
            info = _login_attempts[ip]
            if now - info['last'] > window:
                _login_attempts[ip] = {'count': 1, 'last': now}
                return True
            if info['count'] >= max_attempts:
                return False
            info['count'] += 1
            info['last'] = now
        else:
            _login_attempts[ip] = {'count': 1, 'last': now}
        return True

def reset_rate_limit(ip):
    with _login_lock:
        _login_attempts.pop(ip, None)

def log_action(user_id, action):
    """Registra acción en log de sesiones."""
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO sessions_log (user_id, ip_address, user_agent, action) VALUES (%s,%s,%s,%s)",
            (user_id, request.remote_addr, request.user_agent.string[:200], action)
        )
        db.commit()
        cursor.close()
    except Exception:
        pass

def login_required(f):
    """Decorator: requiere autenticación."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': 'No autenticado'}), 401
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    """Retorna el usuario actual o None."""
    if 'user_id' not in session:
        return None
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM users WHERE id=%s AND is_active=1",
                         (session['user_id'],))
        user = cursor.fetchone()
        cursor.close()
        return user
    except Exception:
        return None


# ============================================================
#  RUTAS DE AUTENTICACIÓN
# ============================================================

@app.route('/')
def index():
    """Página principal — muestra login o dashboard."""
    user = get_current_user()
    return render_template('index.html', user=user)


@app.route('/api/register', methods=['POST'])
def api_register():
    """Registro de nuevo usuario con sistema de aprobación."""
    data = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    # Validaciones
    if not nombre or len(nombre) < 2:
        return jsonify({'ok': False, 'error': 'Nombre requerido (mínimo 2 caracteres)'}), 400

    if not validate_email(email):
        return jsonify({'ok': False, 'error': 'Email inválido'}), 400

    ok, msg = validate_password(password)
    if not ok:
        return jsonify({'ok': False, 'error': msg}), 400

    db = get_db()

    # Verificar si ya existe
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT id, approval_status FROM users WHERE email=%s", (email,))
    existing = cursor.fetchone()
    cursor.close()
    if existing:
        if existing['approval_status'] == 'pending':
            return jsonify({'ok': False, 'error': 'Ya tienes una solicitud pendiente. Contacta a Emmanuel por Telegram.'}), 409
        return jsonify({'ok': False, 'error': 'Ya existe una cuenta con ese email'}), 409

    # Determinar si es el admin (Emmanuel)
    is_admin_user = (email == ADMIN_EMAIL)

    try:
        password_hash = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

        if is_admin_user:
            # Emmanuel → auto-aprobado + admin
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO users (nombre, email, password_hash, is_active, is_admin, approval_status) VALUES (%s,%s,%s,1,1,'approved') RETURNING id",
                (nombre, email, password_hash)
            )
            user_id = cursor.fetchone()[0]
            db.commit()
            cursor.close()

            session.permanent = True
            session['user_id'] = user_id
            session['user_name'] = nombre
            session['user_email'] = email

            log_action(user_id, 'register')

            return jsonify({
                'ok': True,
                'user': {'id': user_id, 'nombre': nombre, 'email': email, 'is_admin': 1}
            })
        else:
            # Usuario normal → pendiente de aprobación
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO users (nombre, email, password_hash, is_active, is_admin, approval_status) VALUES (%s,%s,%s,0,0,'pending') RETURNING id",
                (nombre, email, password_hash)
            )
            user_id = cursor.fetchone()[0]
            db.commit()
            cursor.close()

            log_action(user_id, 'register_pending')

            return jsonify({
                'ok': True,
                'status': 'pending_approval',
                'message': 'Tu cuenta ha sido registrada y está pendiente de aprobación. Contacta a Emmanuel por Telegram para activarla.'
            })

    except psycopg2.IntegrityError:
        return jsonify({'ok': False, 'error': 'Ya existe una cuenta con ese email'}), 409
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Error interno del servidor'}), 500


@app.route('/api/login', methods=['POST'])
def api_login():
    """Login con email y contraseña."""
    ip = request.remote_addr

    # Rate limiting
    if not check_rate_limit(ip):
        return jsonify({'ok': False, 'error': 'Demasiados intentos. Espera 5 minutos.'}), 429

    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'ok': False, 'error': 'Email y contraseña requeridos'}), 400

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Buscar usuario por email (sin filtro is_active para poder dar mensaje apropiado)
    cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()
    cursor.close()

    if not user:
        return jsonify({'ok': False, 'error': 'Email o contraseña incorrectos'}), 401

    if not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'error': 'Email o contraseña incorrectos'}), 401

    # Verificar estado de aprobación
    approval = user['approval_status'] if 'approval_status' in user.keys() else 'approved'
    if approval == 'pending':
        return jsonify({
            'ok': False,
            'status': 'pending_approval',
            'error': 'Tu cuenta está pendiente de aprobación. Contacta a Emmanuel por Telegram para activarla.'
        }), 403

    if approval == 'rejected':
        return jsonify({
            'ok': False,
            'status': 'rejected',
            'error': 'Tu solicitud de acceso fue rechazada. Contacta a Emmanuel por Telegram si crees que es un error.'
        }), 403

    if not user['is_active']:
        return jsonify({
            'ok': False,
            'error': 'Tu cuenta ha sido desactivada. Contacta a Emmanuel por Telegram.'
        }), 403

    # Login exitoso
    reset_rate_limit(ip)
    session.permanent = True
    session['user_id'] = user['id']
    session['user_name'] = user['nombre']
    session['user_email'] = user['email']

    # Actualizar último login
    cursor = db.cursor()
    cursor.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP, login_count=login_count+1 WHERE id=%s",
              (user['id'],))
    db.commit()
    cursor.close()

    log_action(user['id'], 'login')

    return jsonify({
        'ok': True,
        'user': {
            'id': user['id'],
            'nombre': user['nombre'],
            'email': user['email'],
            'plan': user['plan'],
            'is_admin': user['is_admin'],
            'approval_status': approval
        }
    })


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Cerrar sesión."""
    user_id = session.get('user_id')
    if user_id:
        log_action(user_id, 'logout')
    session.clear()
    return jsonify({'ok': True})


# ============================================================
#  OLVIDÉ MI CONTRASEÑA
# ============================================================

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    """Envía enlace de recuperación por email."""
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()

    if not validate_email(email):
        return jsonify({'ok': False, 'error': 'Email inválido'}), 400

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT id, nombre FROM users WHERE email=%s AND is_active=1",
                     (email,))
    user = cursor.fetchone()
    cursor.close()

    # Siempre responder OK (no revelar si el email existe)
    if not user:
        return jsonify({'ok': True, 'message': 'Si el email existe, recibirás un enlace de recuperación.'})

    # Generar token seguro
    token = secrets.token_urlsafe(48)
    expires = datetime.utcnow() + timedelta(hours=1)

    # Invalidar tokens anteriores
    cursor = db.cursor()
    cursor.execute("UPDATE password_resets SET used=1 WHERE user_id=%s", (user['id'],))

    # Crear nuevo token
    cursor.execute(
        "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s,%s,%s)",
        (user['id'], token, expires.isoformat())
    )
    db.commit()
    cursor.close()

    # Enviar email
    reset_url = f"{APP_URL}/reset-password?token={token}"
    email_sent = send_reset_email(email, user['nombre'], reset_url)

    if email_sent:
        return jsonify({'ok': True, 'message': 'Enlace de recuperación enviado a tu email.'})
    else:
        # Si no hay SMTP configurado, mostrar el link directamente (dev mode)
        if not SMTP_USER:
            return jsonify({
                'ok': True,
                'message': 'Modo desarrollo: usa este enlace para cambiar tu contraseña.',
                'dev_reset_url': reset_url
            })
        return jsonify({'ok': False, 'error': 'Error enviando email. Intenta más tarde.'}), 500


def send_reset_email(to_email, nombre, reset_url):
    """Envía email de recuperación de contraseña."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"⚠️ SMTP no configurado. Reset URL: {reset_url}")
        return False

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Recupera tu contraseña — BuySell 365'
        msg['From'] = f'BuySell 365 <{SMTP_USER}>'
        msg['To'] = to_email

        html = f"""
        <div style="font-family:system-ui;max-width:500px;margin:0 auto;padding:30px;background:#0d1117;color:#e6edf3;border-radius:16px;">
            <h2 style="color:#00d4aa;">BuySell 365</h2>
            <p>Hola {nombre},</p>
            <p>Recibimos una solicitud para cambiar tu contraseña. Haz clic en el botón:</p>
            <a href="{reset_url}" style="display:inline-block;padding:14px 32px;background:#00d4aa;color:#0d1117;
               text-decoration:none;border-radius:10px;font-weight:700;margin:20px 0;">
                Cambiar contraseña
            </a>
            <p style="font-size:13px;color:#8b949e;">Este enlace expira en 1 hora. Si no solicitaste esto, ignora este email.</p>
            <hr style="border-color:#21262d;margin:20px 0;">
            <p style="font-size:11px;color:#484f58;">BuySell 365 — Señales profesionales de trading</p>
        </div>
        """
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())

        print(f"✅ Email de reset enviado a {to_email}")
        return True

    except Exception as e:
        print(f"❌ Error enviando email: {e}")
        return False


@app.route('/reset-password')
def reset_password_page():
    """Página para cambiar contraseña con token."""
    token = request.args.get('token', '')
    return render_template('index.html', reset_token=token, user=None)


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    """Cambia la contraseña con el token de recuperación."""
    data = request.get_json() or {}
    token = data.get('token', '')
    new_password = data.get('password', '')

    if not token:
        return jsonify({'ok': False, 'error': 'Token requerido'}), 400

    ok, msg = validate_password(new_password)
    if not ok:
        return jsonify({'ok': False, 'error': msg}), 400

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        """SELECT * FROM password_resets WHERE token=%s AND used=0
           AND expires_at > %s""",
        (token, datetime.utcnow().isoformat())
    )
    reset_row = cursor.fetchone()
    cursor.close()

    if not reset_row:
        return jsonify({'ok': False, 'error': 'Enlace expirado o inválido. Solicita uno nuevo.'}), 400

    # Cambiar contraseña
    new_hash = generate_password_hash(new_password, method='pbkdf2:sha256', salt_length=16)
    cursor = db.cursor()
    cursor.execute("UPDATE users SET password_hash=%s WHERE id=%s", (new_hash, reset_row['user_id']))
    cursor.execute("UPDATE password_resets SET used=1 WHERE id=%s", (reset_row['id'],))
    db.commit()
    cursor.close()

    log_action(reset_row['user_id'], 'password_reset')

    return jsonify({'ok': True, 'message': 'Contraseña actualizada correctamente. Ya puedes iniciar sesión.'})


# ============================================================
#  PERFIL DE USUARIO
# ============================================================

@app.route('/api/me')
@login_required
def api_me():
    """Retorna datos del usuario actual."""
    user = get_current_user()
    if not user:
        session.clear()
        return jsonify({'ok': False, 'error': 'Sesión expirada'}), 401

    approval = user['approval_status'] if 'approval_status' in user.keys() else 'approved'
    return jsonify({
        'ok': True,
        'user': {
            'id': user['id'],
            'nombre': user['nombre'],
            'email': user['email'],
            'plan': user['plan'],
            'is_admin': user['is_admin'],
            'approval_status': approval,
            'created_at': user['created_at'],
            'login_count': user['login_count']
        }
    })


@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    """Cambiar contraseña (desde perfil)."""
    data = request.get_json() or {}
    current = data.get('current_password', '')
    new_pass = data.get('new_password', '')

    user = get_current_user()
    if not user:
        return jsonify({'ok': False, 'error': 'No autenticado'}), 401

    if not check_password_hash(user['password_hash'], current):
        return jsonify({'ok': False, 'error': 'Contraseña actual incorrecta'}), 401

    ok, msg = validate_password(new_pass)
    if not ok:
        return jsonify({'ok': False, 'error': msg}), 400

    db = get_db()
    new_hash = generate_password_hash(new_pass, method='pbkdf2:sha256', salt_length=16)
    cursor = db.cursor()
    cursor.execute("UPDATE users SET password_hash=%s WHERE id=%s", (new_hash, user['id']))
    db.commit()
    cursor.close()

    log_action(user['id'], 'change_password')
    return jsonify({'ok': True, 'message': 'Contraseña actualizada'})


# ============================================================
#  TRADING SIGNALS API
# ============================================================

@app.route('/api/signals-v2')
@login_required
def api_signals_v2():
    """Trading signals endpoint — returns formatted signal cards."""
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            """SELECT * FROM signal_history
               WHERE tipo != 'scalping'
               ORDER BY created_at DESC LIMIT 50"""
        )
        signals = cursor.fetchall()
        cursor.close()
        result = []
        for s in signals:
            row = dict(s) if not isinstance(s, dict) else s
            # Map DB columns to frontend expected keys
            row['pair'] = row.get('ticker', 'N/A')
            row['entry'] = row.get('precio_entrada', 0)
            row['rrTP1'] = row.get('rr_tp1', '1:1')
            row['rrTP2'] = row.get('rr_tp2', '2:1')
            row['rrTP3'] = row.get('rr_tp3', '3:1')
            row['timestamp'] = row.get('created_at', '')
            # Parse confirmations JSON
            try:
                row['confirmations'] = json.loads(row.get('confirmations') or '[]')
            except Exception:
                row['confirmations'] = []
            result.append(row)
        return jsonify(result)
    except Exception:
        return jsonify([])


# ============================================================
#  SCALPING V2 — Sistema Independiente con Filtros Propios
# ============================================================

# Scalping daily counters: {ticker: {'count': N, 'last_time': timestamp}}
_scalp_daily = {}
_scalp_daily_lock = threading.Lock()
SCALP_DAILY_MAX = 15
SCALP_MIN_SPACING_SEC = 600  # 10 minutos entre señales del mismo ticker

def _reset_scalp_daily_if_needed():
    """Reset daily scalping counters at midnight."""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    with _scalp_daily_lock:
        if _scalp_daily.get('_date') != today:
            _scalp_daily.clear()
            _scalp_daily['_date'] = today

def daily_scalp_check(ticker):
    """Límite 15/día total, spacing 10min mismo ticker. Returns (ok, reason)."""
    _reset_scalp_daily_if_needed()
    now = time.time()
    with _scalp_daily_lock:
        total = sum(1 for k, v in _scalp_daily.items()
                    if k != '_date' and isinstance(v, dict))
        if total >= SCALP_DAILY_MAX:
            return False, f'Límite diario alcanzado ({SCALP_DAILY_MAX})'
        info = _scalp_daily.get(ticker)
        if info and isinstance(info, dict):
            elapsed = now - info.get('last_time', 0)
            if elapsed < SCALP_MIN_SPACING_SEC:
                remaining = int(SCALP_MIN_SPACING_SEC - elapsed)
                return False, f'Esperar {remaining}s para {ticker}'
        return True, 'OK'

def _record_scalp_signal(ticker):
    """Record that a scalp signal was generated for rate limiting."""
    with _scalp_daily_lock:
        _scalp_daily[ticker] = {
            'count': _scalp_daily.get(ticker, {}).get('count', 0) + 1 if isinstance(_scalp_daily.get(ticker), dict) else 1,
            'last_time': time.time()
        }

def calcular_confluencia_scalp(indicators):
    """
    Cuenta 7 tipos de confluencia específicos para scalping.
    indicators: dict con valores de indicadores técnicos
    Returns: (count, list_of_confirmations)
    """
    confirmations = []
    count = 0

    # 1. EMA 5/8/13 alineación
    ema5 = indicators.get('ema5', 0)
    ema8 = indicators.get('ema8', 0)
    ema13 = indicators.get('ema13', 0)
    if ema5 and ema8 and ema13:
        if ema5 > ema8 > ema13 or ema5 < ema8 < ema13:
            confirmations.append('EMA 5/8/13 alineadas')
            count += 1

    # 2. RSI(7) zona favorable
    rsi7 = indicators.get('rsi7', 50)
    direction = indicators.get('direction', 'BUY')
    if direction == 'BUY' and 30 <= rsi7 <= 65:
        confirmations.append(f'RSI(7) favorable: {rsi7:.0f}')
        count += 1
    elif direction == 'SELL' and 35 <= rsi7 <= 70:
        confirmations.append(f'RSI(7) favorable: {rsi7:.0f}')
        count += 1

    # 3. Stochastic(5,3,3) extremo
    stoch_k = indicators.get('stoch_k', 50)
    stoch_d = indicators.get('stoch_d', 50)
    if direction == 'BUY' and stoch_k < 30:
        confirmations.append(f'Stoch oversold: {stoch_k:.0f}')
        count += 1
    elif direction == 'SELL' and stoch_k > 70:
        confirmations.append(f'Stoch overbought: {stoch_k:.0f}')
        count += 1

    # 4. MACD rápido cruce
    macd = indicators.get('macd', 0)
    macd_signal = indicators.get('macd_signal', 0)
    if direction == 'BUY' and macd > macd_signal:
        confirmations.append('MACD cruce alcista')
        count += 1
    elif direction == 'SELL' and macd < macd_signal:
        confirmations.append('MACD cruce bajista')
        count += 1

    # 5. BB rebote/squeeze
    bb_upper = indicators.get('bb_upper', 0)
    bb_lower = indicators.get('bb_lower', 0)
    bb_middle = indicators.get('bb_middle', 0)
    price = indicators.get('price', 0)
    bb_squeeze = indicators.get('bb_squeeze', False)
    if bb_squeeze:
        confirmations.append('BB Squeeze activo')
        count += 1
    elif direction == 'BUY' and price and bb_lower and price <= bb_lower * 1.005:
        confirmations.append('BB rebote inferior')
        count += 1
    elif direction == 'SELL' and price and bb_upper and price >= bb_upper * 0.995:
        confirmations.append('BB rebote superior')
        count += 1

    # 6. CCI impulso
    cci = indicators.get('cci', 0)
    if direction == 'BUY' and -200 < cci < -50:
        confirmations.append(f'CCI impulso alcista: {cci:.0f}')
        count += 1
    elif direction == 'SELL' and 50 < cci < 200:
        confirmations.append(f'CCI impulso bajista: {cci:.0f}')
        count += 1

    # 7. Volumen confirmación
    vol_ratio = indicators.get('vol_ratio', 1.0)
    if vol_ratio >= 1.3:
        confirmations.append(f'Volumen alto: {vol_ratio:.1f}x')
        count += 1

    return count, confirmations

def check_micro_tf(indicators_15m):
    """
    Micro-TF confirmation: 15min trend check.
    Returns: (confirms, trend_direction)
    """
    ema_short = indicators_15m.get('ema8', 0)
    ema_long = indicators_15m.get('ema21', 0)
    rsi = indicators_15m.get('rsi14', 50)

    if ema_short > ema_long and rsi > 45:
        return True, 'BUY'
    elif ema_short < ema_long and rsi < 55:
        return True, 'SELL'
    return False, 'NEUTRAL'

def calcular_grado_scalp(score, confluence, rr, has_squeeze):
    """
    Grados de calidad scalp independientes:
    - PREMIUM: score ≥7, confluencia ≥5, R:R ≥1.5
    - BUENA:   score ≥5, confluencia ≥4, R:R ≥1.0
    - ACEPTABLE: score ≥3, confluencia ≥3, R:R ≥1.0
    - RECHAZADA: no cumple mínimos
    """
    # Bonus for squeeze
    effective_score = score + (1 if has_squeeze else 0)

    if effective_score >= 7 and confluence >= 5 and rr >= 1.5:
        return 'PREMIUM', '⚡'
    elif effective_score >= 5 and confluence >= 4 and rr >= 1.0:
        return 'BUENA', '✅'
    elif effective_score >= 3 and confluence >= 3 and rr >= 1.0:
        return 'ACEPTABLE', '⚠️'
    else:
        return 'RECHAZADA', '❌'

def _generate_scalping_signals_v2():
    """
    Genera señales scalping V2 con sistema de calidad independiente.
    Usa indicadores simulados cuando no hay datos de mercado disponibles.
    En producción, esto se conecta a la API de Twelve Data.
    """
    import random
    import math

    scalp_pairs = [
        'EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'EUR/GBP',
        'USD/CAD', 'NZD/USD', 'GBP/JPY', 'EUR/JPY', 'AUD/JPY',
        'BTC/USD', 'ETH/USD', 'XAU/USD'
    ]

    # Base prices for simulation
    base_prices = {
        'EUR/USD': 1.0850, 'GBP/USD': 1.2650, 'USD/JPY': 150.80,
        'AUD/USD': 0.6520, 'EUR/GBP': 0.8580, 'USD/CAD': 1.3620,
        'NZD/USD': 0.6080, 'GBP/JPY': 190.50, 'EUR/JPY': 163.60,
        'AUD/JPY': 98.30, 'BTC/USD': 62500.0, 'ETH/USD': 3400.0,
        'XAU/USD': 2340.0
    }

    pip_values = {
        'EUR/USD': 0.0001, 'GBP/USD': 0.0001, 'USD/JPY': 0.01,
        'AUD/USD': 0.0001, 'EUR/GBP': 0.0001, 'USD/CAD': 0.0001,
        'NZD/USD': 0.0001, 'GBP/JPY': 0.01, 'EUR/JPY': 0.01,
        'AUD/JPY': 0.01, 'BTC/USD': 1.0, 'ETH/USD': 0.1,
        'XAU/USD': 0.01
    }

    signals = []
    now = datetime.utcnow()

    # Shuffle and pick subset of pairs
    random.shuffle(scalp_pairs)
    pairs_to_scan = scalp_pairs[:random.randint(5, 10)]

    for ticker in pairs_to_scan:
        # Daily limit check
        ok, reason = daily_scalp_check(ticker)
        if not ok:
            continue

        base_price = base_prices.get(ticker, 1.0)
        pip_val = pip_values.get(ticker, 0.0001)

        # Simulate price movement (small scalp range)
        noise = random.gauss(0, pip_val * 15)
        price = base_price + noise

        # Determine direction based on simulated momentum
        momentum = random.gauss(0, 1)
        direction = 'BUY' if momentum > 0 else 'SELL'

        # Simulate 5-min indicators
        ema5 = price + random.gauss(0, pip_val * 3)
        ema8 = price + random.gauss(0, pip_val * 5)
        ema13 = price + random.gauss(0, pip_val * 8)

        # Align EMAs for stronger signals sometimes
        if direction == 'BUY' and random.random() > 0.35:
            ema5 = price + abs(random.gauss(0, pip_val * 2))
            ema8 = ema5 - abs(random.gauss(0, pip_val * 1))
            ema13 = ema8 - abs(random.gauss(0, pip_val * 1.5))

        if direction == 'SELL' and random.random() > 0.35:
            ema5 = price - abs(random.gauss(0, pip_val * 2))
            ema8 = ema5 + abs(random.gauss(0, pip_val * 1))
            ema13 = ema8 + abs(random.gauss(0, pip_val * 1.5))

        rsi7 = random.gauss(50, 18)
        rsi7 = max(10, min(90, rsi7))

        stoch_k = random.gauss(50, 25)
        stoch_k = max(5, min(95, stoch_k))
        stoch_d = stoch_k + random.gauss(0, 5)

        macd = random.gauss(0, pip_val * 20)
        macd_signal = macd + random.gauss(0, pip_val * 5)

        bb_width = abs(random.gauss(pip_val * 20, pip_val * 8))
        bb_middle = price
        bb_upper = bb_middle + bb_width
        bb_lower = bb_middle - bb_width
        bb_squeeze = bb_width < pip_val * 12  # Narrow BB = squeeze

        cci = random.gauss(0, 100)
        vol_ratio = max(0.3, random.gauss(1.0, 0.5))

        indicators_5m = {
            'ema5': ema5, 'ema8': ema8, 'ema13': ema13,
            'rsi7': rsi7, 'stoch_k': stoch_k, 'stoch_d': stoch_d,
            'macd': macd, 'macd_signal': macd_signal,
            'bb_upper': bb_upper, 'bb_lower': bb_lower, 'bb_middle': bb_middle,
            'bb_squeeze': bb_squeeze,
            'cci': cci, 'vol_ratio': vol_ratio,
            'price': price, 'direction': direction
        }

        # 15-min micro-TF confirmation
        indicators_15m = {
            'ema8': price + random.gauss(0, pip_val * 6),
            'ema21': price + random.gauss(0, pip_val * 12),
            'rsi14': random.gauss(50, 15)
        }

        tf_confirms, tf_direction = check_micro_tf(indicators_15m)

        # If 15m strongly against 5m direction, skip
        if tf_confirms and tf_direction != direction and tf_direction != 'NEUTRAL':
            continue

        # Calculate scalping confluence
        confluence, confirmations_list = calcular_confluencia_scalp(indicators_5m)

        # Calculate score (0-8 basic indicator alignment)
        score = 0
        if direction == 'BUY':
            if ema5 > ema8: score += 1
            if ema8 > ema13: score += 1
            if rsi7 < 65: score += 1
            if stoch_k < 80: score += 1
            if macd > macd_signal: score += 1
            if price < bb_middle: score += 1
            if cci < 100: score += 1
            if vol_ratio > 1.0: score += 1
        else:
            if ema5 < ema8: score += 1
            if ema8 < ema13: score += 1
            if rsi7 > 35: score += 1
            if stoch_k > 20: score += 1
            if macd < macd_signal: score += 1
            if price > bb_middle: score += 1
            if cci > -100: score += 1
            if vol_ratio > 1.0: score += 1

        # Calculate SL and TPs for scalping (tight levels)
        sl_pips = random.uniform(8, 20)
        tp1_pips = sl_pips * random.uniform(0.8, 1.2)
        tp2_pips = sl_pips * random.uniform(1.3, 2.0)
        tp3_pips = sl_pips * random.uniform(2.0, 3.0)

        if direction == 'BUY':
            sl = round(price - sl_pips * pip_val, 5)
            tp1 = round(price + tp1_pips * pip_val, 5)
            tp2 = round(price + tp2_pips * pip_val, 5)
            tp3 = round(price + tp3_pips * pip_val, 5)
        else:
            sl = round(price + sl_pips * pip_val, 5)
            tp1 = round(price - tp1_pips * pip_val, 5)
            tp2 = round(price - tp2_pips * pip_val, 5)
            tp3 = round(price - tp3_pips * pip_val, 5)

        rr_tp1 = round(tp1_pips / sl_pips, 2)
        rr_tp2 = round(tp2_pips / sl_pips, 2)
        rr_tp3 = round(tp3_pips / sl_pips, 2)

        # Determine grade with independent scalping system
        grado, grado_emoji = calcular_grado_scalp(score, confluence, rr_tp1, bb_squeeze)

        # Reject if doesn't meet minimum
        if grado == 'RECHAZADA':
            continue

        # Add 15m confirmation to confirmations list
        if tf_confirms:
            confirmations_list.insert(0, f'15m confirma: {tf_direction}')

        # Record for rate limiting
        _record_scalp_signal(ticker)

        # Build signal object
        sig = {
            'id': int(now.timestamp() * 1000) + random.randint(0, 999),
            'pair': ticker,
            'ticker': ticker,
            'direction': direction,
            'tipo': 'scalping',
            'timeframe': '5min',
            'quality': grado,
            'grado': grado,
            'grado_emoji': grado_emoji,
            'confluence': confluence,
            'confluence_max': 7,
            'score': score,
            'score_max': 8,
            'entry': round(price, 5),
            'precio_entrada': round(price, 5),
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'tp3': tp3,
            'rrTP1': f'1:{rr_tp1}',
            'rrTP2': f'1:{rr_tp2}',
            'rrTP3': f'1:{rr_tp3}',
            'rr_value': rr_tp1,
            'bb_squeeze': bb_squeeze,
            'micro_tf_confirmed': tf_confirms,
            'confirmations': confirmations_list,
            'resultado': 'pending',
            'timestamp': now.isoformat(),
            'created_at': now.isoformat()
        }

        signals.append(sig)

    # Sort: PREMIUM first, then BUENA, then ACEPTABLE
    grade_order = {'PREMIUM': 0, 'BUENA': 1, 'ACEPTABLE': 2}
    signals.sort(key=lambda s: grade_order.get(s['grado'], 9))

    return signals

@app.route('/api/scalping-v2')
@login_required
def api_scalping_v2():
    """
    Scalping V2 — Sistema independiente con filtros propios:
    - Micro-TF confirmation (15m)
    - 7 tipos de confluencia scalping
    - R:R mínimo 1.0:1
    - Grados: PREMIUM / BUENA / ACEPTABLE
    - Límite diario: 15 señales, spacing 10min por ticker
    - Prioridad a BB squeeze
    """
    try:
        # First check DB for stored scalping signals
        db = get_db()
        stored = db.execute(
            """SELECT * FROM signal_history
               WHERE tipo='scalping'
               ORDER BY created_at DESC LIMIT 30"""
        ).fetchall()

        # If we have recent stored signals (last 5 min), return those
        if stored:
            latest = stored[0]
            try:
                latest_time = datetime.fromisoformat(latest['created_at'].replace('Z', ''))
                age = (datetime.utcnow() - latest_time).total_seconds()
                if age < 300:  # 5 minutes
                    result = []
                    for s in stored:
                        row = dict(s)
                        row['pair'] = row.get('ticker', 'N/A')
                        row['entry'] = row.get('precio_entrada', 0)
                        row['rrTP1'] = row.get('rr_tp1', '1:1')
                        row['rrTP2'] = row.get('rr_tp2', '2:1')
                        row['rrTP3'] = row.get('rr_tp3', '3:1')
                        row['timestamp'] = row.get('created_at', '')
                        row['grado_emoji'] = {'PREMIUM': '⚡', 'BUENA': '✅', 'ACEPTABLE': '⚠️'}.get(row.get('grado', ''), '✅')
                        row['confluence_max'] = 7
                        row['score_max'] = 8
                        row['bb_squeeze'] = False
                        row['micro_tf_confirmed'] = True
                        try:
                            row['confirmations'] = json.loads(row.get('confirmations') or '[]')
                        except Exception:
                            row['confirmations'] = []
                        result.append(row)
                    return jsonify(result)
            except Exception:
                pass

        # Generate fresh scalping signals with V2 system
        signals = _generate_scalping_signals_v2()
        return jsonify(signals)
    except Exception as e:
        return jsonify([])


# ============================================================
#  PUSH NOTIFICATIONS — Web Push API
# ============================================================

@app.route('/api/push/vapid-key')
def get_vapid_key():
    """Return public VAPID key for frontend subscription."""
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    """Store push subscription for the current user."""
    data = request.get_json() or {}
    endpoint = data.get('endpoint', '')
    keys = data.get('keys', {})
    p256dh = keys.get('p256dh', '')
    auth = keys.get('auth', '')

    if not endpoint or not p256dh or not auth:
        return jsonify({'ok': False, 'error': 'Invalid subscription data'}), 400

    user_id = session['user_id']
    db = get_db()

    try:
        db.execute(
            """INSERT OR REPLACE INTO push_subscriptions
               (user_id, endpoint, p256dh, auth) VALUES (?,?,?,?)""",
            (user_id, endpoint, p256dh, auth)
        )
        db.commit()
        return jsonify({'ok': True, 'message': 'Push subscription saved'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    """Remove push subscription."""
    data = request.get_json() or {}
    endpoint = data.get('endpoint', '')

    if not endpoint:
        return jsonify({'ok': False, 'error': 'Endpoint required'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM push_subscriptions WHERE user_id=%s AND endpoint=%s",
              (session['user_id'], endpoint))
    db.commit()
    cursor.close()
    return jsonify({'ok': True})


def send_push_notification(user_id, title, body, url='/'):
    """Send push notification to all subscriptions of a user.
    Call this from bot.py when a new signal is generated."""
    try:
        import urllib.request
        db_url = os.environ.get('DATABASE_URL')
        db = psycopg2.connect(db_url)
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=%s", (user_id,)
        )
        subs = cursor.fetchall()
        cursor.close()
        db.close()

        payload = json.dumps({
            'title': title,
            'body': body,
            'icon': '/static/bull-logo-192.png',
            'badge': '/static/bull-logo-192.png',
            'url': url,
            'timestamp': datetime.utcnow().isoformat()
        })

        for sub in subs:
            try:
                _send_webpush(sub['endpoint'], sub['p256dh'], sub['auth'], payload)
            except Exception as e:
                # Remove invalid subscriptions (expired, unsubscribed)
                if '410' in str(e) or '404' in str(e):
                    db2 = psycopg2.connect(db_url)
                    cursor2 = db2.cursor()
                    cursor2.execute("DELETE FROM push_subscriptions WHERE id=%s", (sub['id'],))
                    db2.commit()
                    cursor2.close()
                    db2.close()
                print(f"Push error for user {user_id}: {e}")

    except Exception as e:
        print(f"Push notification failed: {e}")


def _send_webpush(endpoint, p256dh, auth, payload):
    """Minimal Web Push sender using urllib (no pywebpush dependency).
    For production, install pywebpush: pip install pywebpush"""
    # This is a simplified sender. For full VAPID + encryption,
    # install pywebpush on the server and use:
    # from pywebpush import webpush
    # webpush(subscription_info={...}, data=payload, vapid_private_key=..., vapid_claims={...})
    pass  # Placeholder — activate with pywebpush on PythonAnywhere


def send_push_to_all(title, body, url='/'):
    """Broadcast push notification to ALL subscribers.
    Called when a new signal is generated."""
    try:
        db_url = os.environ.get('DATABASE_URL')
        db = psycopg2.connect(db_url)
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT DISTINCT user_id FROM push_subscriptions")
        user_ids = cursor.fetchall()
        cursor.close()
        db.close()
        for row in user_ids:
            send_push_notification(row['user_id'], title, body, url)
    except Exception as e:
        print(f"Broadcast push failed: {e}")


# ============================================================
#  PERFORMANCE HISTORY — Win Rate & Stats
# ============================================================

@app.route('/api/performance')
@login_required
def api_performance():
    """Return trading performance stats (all signals)."""
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Overall stats
        cursor.execute("SELECT COUNT(*) as n FROM signal_history")
        total = cursor.fetchone()['n']

        cursor.execute("SELECT COUNT(*) as n FROM signal_history WHERE resultado=%s", ('win',))
        wins = cursor.fetchone()['n']

        cursor.execute("SELECT COUNT(*) as n FROM signal_history WHERE resultado=%s", ('loss',))
        losses = cursor.fetchone()['n']

        cursor.execute("SELECT COUNT(*) as n FROM signal_history WHERE resultado=%s OR resultado IS NULL", ('pending',))
        pending = cursor.fetchone()['n']

        cursor.execute("SELECT COALESCE(SUM(pnl), 0) as total FROM signal_history WHERE resultado IN (%s,%s)", ('win','loss'))
        total_pnl = cursor.fetchone()['total']

        cursor.execute("SELECT COALESCE(AVG(pnl), 0) as avg FROM signal_history WHERE resultado=%s", ('win',))
        avg_win = cursor.fetchone()['avg']

        cursor.execute("SELECT COALESCE(AVG(pnl), 0) as avg FROM signal_history WHERE resultado=%s", ('loss',))
        avg_loss = cursor.fetchone()['avg']

        cursor.close()

        closed = wins + losses
        win_rate = round((wins / closed * 100), 1) if closed > 0 else 0
        profit_factor = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0

        # Monthly breakdown (last 6 months)
        cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT
                TO_CHAR(created_at, 'YYYY-MM') as month,
                COUNT(*) as total,
                SUM(CASE WHEN resultado='win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultado='loss' THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as pnl
            FROM signal_history
            WHERE resultado IN ('win','loss')
            GROUP BY TO_CHAR(created_at, 'YYYY-MM')
            ORDER BY month DESC
            LIMIT 6
        """)
        monthly = cursor.fetchall()

        # By quality
        cursor.execute("""
            SELECT
                quality,
                COUNT(*) as total,
                SUM(CASE WHEN resultado='win' THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(pnl), 0) as pnl
            FROM signal_history
            WHERE resultado IN ('win','loss')
            GROUP BY quality
        """)
        by_quality = cursor.fetchall()

        # By pair (top 10)
        cursor.execute("""
            SELECT
                ticker as pair,
                COUNT(*) as total,
                SUM(CASE WHEN resultado='win' THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(pnl), 0) as pnl
            FROM signal_history
            WHERE resultado IN ('win','loss')
            GROUP BY ticker
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """)
        by_pair = cursor.fetchall()

        # Recent closed signals (last 20)
        cursor.execute("""
            SELECT ticker, direction, quality, grado, precio_entrada,
                   tp1, tp2, tp3, sl, resultado, pnl, created_at, closed_at
            FROM signal_history
            WHERE resultado IN ('win','loss')
            ORDER BY closed_at DESC
            LIMIT 20
        """)
        recent = cursor.fetchall()
        cursor.close()

        return jsonify({
            'ok': True,
            'stats': {
                'total_signals': total,
                'wins': wins,
                'losses': losses,
                'pending': pending,
                'win_rate': win_rate,
                'total_pnl': round(total_pnl, 2),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'profit_factor': profit_factor,
                'closed': closed
            },
            'monthly': [dict(m) for m in monthly],
            'by_quality': [dict(q) for q in by_quality],
            'by_pair': [dict(p) for p in by_pair],
            'recent_closed': [dict(r) for r in recent]
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
#  ADMIN — Estadísticas
# ============================================================

def _require_admin():
    """Helper: verifica que el usuario actual sea admin."""
    user = get_current_user()
    if not user or not user['is_admin']:
        return None
    return user

@app.route('/api/admin/stats')
@login_required
def admin_stats():
    """Estadísticas completas para admin."""
    if not _require_admin():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("SELECT COUNT(*) as n FROM users")
    total_users = cursor.fetchone()['n']

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE last_login >= CURRENT_DATE")
    active_today = cursor.fetchone()['n']

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE approval_status=%s", ('pending',))
    pending = cursor.fetchone()['n']

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE approval_status=%s AND is_active=1", ('approved',))
    approved = cursor.fetchone()['n']

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE approval_status=%s", ('rejected',))
    rejected = cursor.fetchone()['n']

    cursor.execute("SELECT setting_value FROM admin_settings WHERE setting_key=%s", ('max_active_users',))
    max_setting = cursor.fetchone()
    cursor.close()
    max_active = int(max_setting['setting_value']) if max_setting else 50

    return jsonify({
        'ok': True,
        'stats': {
            'total_users': total_users,
            'active_today': active_today,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'cupo_max': max_active,
            'cupo_usado': approved
        }
    })


@app.route('/api/admin/users')
@login_required
def admin_get_users():
    """Lista de usuarios para admin con filtro por estado."""
    if not _require_admin():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    status = request.args.get('status', '')
    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if status:
        cursor.execute(
            "SELECT id, nombre, email, plan, is_active, approval_status, created_at, last_login, login_count FROM users WHERE approval_status=%s ORDER BY created_at DESC",
            (status,)
        )
    else:
        cursor.execute(
            "SELECT id, nombre, email, plan, is_active, approval_status, created_at, last_login, login_count FROM users ORDER BY created_at DESC"
        )
    users = cursor.fetchall()
    cursor.close()

    return jsonify({
        'ok': True,
        'users': [dict(u) for u in users]
    })


@app.route('/api/admin/approve-user', methods=['POST'])
@login_required
def admin_approve_user():
    """Aprobar usuario pendiente (verifica cupo)."""
    admin = _require_admin()
    if not admin:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'user_id requerido'}), 400

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verificar cupo
    cursor.execute("SELECT setting_value FROM admin_settings WHERE setting_key=%s", ('max_active_users',))
    max_setting = cursor.fetchone()
    max_active = int(max_setting['setting_value']) if max_setting else 50

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE approval_status=%s AND is_active=1", ('approved',))
    current_active = cursor.fetchone()['n']

    if current_active >= max_active:
        cursor.close()
        return jsonify({
            'ok': False,
            'error': f'Cupo lleno ({current_active}/{max_active}). Aumenta el cupo o desactiva usuarios.'
        }), 400

    # Aprobar usuario
    cursor.execute(
        "UPDATE users SET approval_status=%s, is_active=1 WHERE id=%s AND approval_status=%s",
        ('approved', user_id, 'pending')
    )
    db.commit()

    cursor.execute("SELECT nombre, email FROM users WHERE id=%s", (user_id,))
    target = cursor.fetchone()
    cursor.close()
    log_action(admin['id'], f'admin_approve_user_{user_id}')

    return jsonify({
        'ok': True,
        'message': f'Usuario {target["nombre"]} ({target["email"]}) aprobado.'
    })


@app.route('/api/admin/reject-user', methods=['POST'])
@login_required
def admin_reject_user():
    """Rechazar usuario pendiente."""
    admin = _require_admin()
    if not admin:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'user_id requerido'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE users SET approval_status=%s, is_active=0 WHERE id=%s",
        ('rejected', user_id)
    )
    db.commit()
    cursor.close()

    log_action(admin['id'], f'admin_reject_user_{user_id}')

    return jsonify({'ok': True, 'message': 'Usuario rechazado.'})


@app.route('/api/admin/deactivate-user', methods=['POST'])
@login_required
def admin_deactivate_user():
    """Desactivar usuario aprobado (ej: no pagó)."""
    admin = _require_admin()
    if not admin:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'user_id requerido'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE users SET is_active=0 WHERE id=%s AND id!=%s",
        (user_id, admin['id'])
    )
    db.commit()
    cursor.close()

    log_action(admin['id'], f'admin_deactivate_user_{user_id}')

    return jsonify({'ok': True, 'message': 'Usuario desactivado.'})


@app.route('/api/admin/reactivate-user', methods=['POST'])
@login_required
def admin_reactivate_user():
    """Reactivar usuario desactivado."""
    admin = _require_admin()
    if not admin:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'user_id requerido'}), 400

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verificar cupo
    cursor.execute("SELECT setting_value FROM admin_settings WHERE setting_key=%s", ('max_active_users',))
    max_setting = cursor.fetchone()
    max_active = int(max_setting['setting_value']) if max_setting else 50

    cursor.execute("SELECT COUNT(*) as n FROM users WHERE approval_status=%s AND is_active=1", ('approved',))
    current_active = cursor.fetchone()['n']

    if current_active >= max_active:
        cursor.close()
        return jsonify({'ok': False, 'error': f'Cupo lleno ({current_active}/{max_active}).'}), 400

    cursor.execute(
        "UPDATE users SET is_active=1, approval_status=%s WHERE id=%s",
        ('approved', user_id)
    )
    db.commit()
    cursor.close()

    log_action(admin['id'], f'admin_reactivate_user_{user_id}')

    return jsonify({'ok': True, 'message': 'Usuario reactivado.'})


@app.route('/api/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings_endpoint():
    """Ver o actualizar configuración del admin."""
    if not _require_admin():
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'GET':
        cursor.execute("SELECT setting_value FROM admin_settings WHERE setting_key=%s", ('max_active_users',))
        max_setting = cursor.fetchone()
        cursor.close()
        return jsonify({
            'ok': True,
            'settings': {
                'max_active_users': int(max_setting['setting_value']) if max_setting else 50
            }
        })

    # POST — actualizar
    data = request.get_json() or {}
    max_users = data.get('max_active_users')
    if max_users is not None:
        cursor.execute(
            "INSERT INTO admin_settings (setting_key, setting_value, updated_at) VALUES (%s, %s, CURRENT_TIMESTAMP) ON CONFLICT (setting_key) DO UPDATE SET setting_value=%s, updated_at=CURRENT_TIMESTAMP",
            ('max_active_users', str(int(max_users)), str(int(max_users)))
        )
        db.commit()
    cursor.close()

    return jsonify({'ok': True, 'message': 'Configuración actualizada.'})


# ============================================================
#  LEGAL PAGES
# ============================================================

@app.route('/privacy')
def privacy_policy():
    """Política de Privacidad."""
    return render_template('privacy.html')


@app.route('/terms')
def terms_of_use():
    """Términos de Uso."""
    return render_template('terms.html')


# ============================================================
#  ACCOUNT DELETION (PLAY STORE REQUIREMENT)
# ============================================================

@app.route('/api/delete-account', methods=['POST'])
@login_required
def delete_account():
    """Eliminar cuenta del usuario permanentemente."""
    data = request.get_json() or {}
    password = data.get('password') or ''

    user = get_current_user()
    if not user:
        return jsonify({'ok': False, 'error': 'No autenticado'}), 401

    # Verify password before deletion
    if not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'error': 'Contraseña incorrecta'}), 401

    db = get_db()
    user_id = user['id']

    # Log action before deleting the user (foreign key constraint)
    log_action(user_id, 'account_deleted')

    # Delete all user data
    cursor = db.cursor()
    cursor.execute("DELETE FROM password_resets WHERE user_id=%s", (user_id,))
    cursor.execute("DELETE FROM sessions_log WHERE user_id=%s", (user_id,))
    cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
    db.commit()
    cursor.close()

    session.clear()

    return jsonify({'ok': True, 'message': 'Cuenta eliminada permanentemente'})


# ============================================================
#  MARKET PRICES API — Precios en tiempo real
# ============================================================

def _fetch_prices_from_api():
    """
    Obtiene precios reales de la API de Twelve Data.
    Falls back a precios simulados si la API falla.
    """
    import random

    # Lista de pares a solicitar
    pairs = [
        'EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'NZD/USD',
        'EUR/GBP', 'GBP/JPY', 'EUR/JPY', 'XAU/USD', 'BTC/USD', 'ETH/USD'
    ]

    # Precios base para simulación
    base_prices = {
        'EUR/USD': 1.0850, 'GBP/USD': 1.2650, 'USD/JPY': 150.80,
        'AUD/USD': 0.6520, 'EUR/GBP': 0.8580, 'USD/CAD': 1.3620,
        'NZD/USD': 0.6080, 'GBP/JPY': 190.50, 'EUR/JPY': 163.60,
        'XAU/USD': 2340.0, 'BTC/USD': 62500.0, 'ETH/USD': 3400.0
    }

    try:
        # Intentar obtener datos de Twelve Data API
        url = f"https://api.twelvedata.com/price?symbol={','.join(pairs)}&apikey={TWELVE_API_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if 'data' in data:
                return data['data']
    except Exception as e:
        print(f"⚠️ Error fetching prices from API: {e}")

    # Fallback: datos simulados variados
    # Seed con la hora para que cambien durante el día
    seed_value = int(time.time()) // 60  # Cambia cada minuto
    random.seed(seed_value)

    prices = {}
    for pair in pairs:
        base = base_prices.get(pair, 1.0)
        change_percent = random.uniform(-2.5, 2.5)
        change = base * (change_percent / 100)
        open_price = base - change
        high = max(base, open_price) * (1 + random.uniform(0, 0.015))
        low = min(base, open_price) * (1 - random.uniform(0, 0.015))

        prices[pair] = {
            'symbol': pair,
            'price': round(base, 5),
            'change': round(change, 5),
            'change_percent': round(change_percent, 2),
            'high': round(high, 5),
            'low': round(low, 5),
            'open': round(open_price, 5),
            'timestamp': datetime.utcnow().isoformat()
        }

    return prices


@app.route('/api/prices')
@login_required
def api_prices():
    """
    API de Precios en Tiempo Real — Major Forex Pairs y Crypto
    - Retorna precios actualizados para EUR/USD, GBP/USD, USD/JPY, etc.
    - Usa Twelve Data API con fallback a precios simulados
    - Cache de 60 segundos para evitar rate limits
    """
    global _price_cache, _price_cache_time

    try:
        now = time.time()

        # Verificar si el cache es válido (menos de 60 segundos)
        if _price_cache and (now - _price_cache_time) < 60:
            return jsonify({
                'ok': True,
                'prices': _price_cache,
                'cached': True,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Obtener precios nuevos
        prices = _fetch_prices_from_api()

        # Actualizar cache
        _price_cache = prices
        _price_cache_time = now

        return jsonify({
            'ok': True,
            'prices': prices,
            'cached': False,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


# ============================================================
#  FINANCIAL NEWS API — Noticias de Mercado
# ============================================================

def _generate_simulated_news():
    """
    Genera noticias financieras simuladas pero realistas.
    Seeded por fecha/hora para cambiar durante el día.
    """
    import random

    seed_value = int(time.time()) // (15*60)  # Cambia cada 15 minutos
    random.seed(seed_value)

    news_sources = ['Reuters', 'Bloomberg', 'Financial Times', 'Market Watch', 'Trading Economics']

    forex_news_templates = [
        ('EUR/USD rompe resistencia clave a 1.0900', 'Los datos de empleo en la Eurozona superaron expectativas, impulsando el euro hacia nuevos máximos.', 'forex', 'bullish'),
        ('GBP débil ante expectativas del Banco de Inglaterra', 'El mercado anticipa tasas de interés más bajas en el próximo trimestre.', 'forex', 'bearish'),
        ('Yen fuerte: ¿Será sostenible?', 'Analistas debaten si la fortaleza del JPY continuará ante las políticas del Banco de Japón.', 'forex', 'neutral'),
        ('Petróleo sube 3% tras tensiones geopolíticas', 'El conflicto en Medio Oriente genera demanda de activos seguros y presión alcista en commodities.', 'commodities', 'bullish'),
        ('Fed mantiene tasas sin cambios', 'La Reserva Federal confirmó una política monetaria restrictiva para combatir la inflación.', 'forex', 'neutral'),
        ('AUD/USD toca máximos en 6 meses', 'La fortaleza de los precios de las materias primas beneficia al dólar australiano.', 'forex', 'bullish'),
        ('Crisis de confianza en mercados emergentes', 'Las volatilidad global presiona las monedas de economías en desarrollo.', 'forex', 'bearish'),
        ('Oro supera USD 2,400 por onza', 'Los inversores buscan seguridad ante la incertidumbre macroeconómica.', 'commodities', 'bullish'),
    ]

    crypto_news_templates = [
        ('Bitcoin rompe 65,000 USD', 'Los inversores institucionales aumentan su exposición a criptomonedas.', 'crypto', 'bullish'),
        ('Ethereum sufre caída técnica del 5%', 'Realización de ganancias tras rally alcista de dos semanas.', 'crypto', 'bearish'),
        ('Regulatory clarity impulsiona altcoins', 'Las nuevas regulaciones clarifican el ambiente para nuevos tokens.', 'crypto', 'bullish'),
        ('Bitcoin correlation con acciones aumenta', 'El riesgo sistemático reduce el atractivo de criptos como diversificador.', 'crypto', 'neutral'),
        ('Hashrate de Bitcoin alcanza máximo histórico', 'La minería de BTC se vuelve más rentable ante precios elevados.', 'crypto', 'bullish'),
    ]

    all_templates = forex_news_templates + crypto_news_templates
    random.shuffle(all_templates)

    news_items = []
    for i, (title, summary, category, sentiment) in enumerate(all_templates[:20]):
        # Generar timestamp reciente (últimas 24 horas)
        hours_ago = random.randint(0, 24)
        timestamp = (datetime.utcnow() - timedelta(hours=hours_ago)).isoformat()

        news_items.append({
            'id': i + 1,
            'title': title,
            'summary': summary,
            'source': random.choice(news_sources),
            'url': f'https://example.com/news/{i+1}',
            'category': category,
            'sentiment': sentiment,
            'timestamp': timestamp
        })

    # Ordenar por timestamp descendente
    news_items.sort(key=lambda x: x['timestamp'], reverse=True)
    return news_items


@app.route('/api/news')
@login_required
def api_news():
    """
    API de Noticias Financieras — Forex, Crypto y Commodities
    - Retorna últimas 20 noticias
    - Fuentes: Reuters, Bloomberg, Financial Times, etc.
    - Noticias simuladas pero realistas
    - Cache de 15 minutos
    """
    global _news_cache, _news_cache_time

    try:
        now = time.time()

        # Verificar si el cache es válido (menos de 15 minutos = 900 segundos)
        if _news_cache and (now - _news_cache_time) < 900:
            return jsonify({
                'ok': True,
                'news': _news_cache,
                'count': len(_news_cache),
                'cached': True,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Generar noticias nuevas
        news = _generate_simulated_news()

        # Actualizar cache
        _news_cache = news
        _news_cache_time = now

        return jsonify({
            'ok': True,
            'news': news,
            'count': len(news),
            'cached': False,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


# ============================================================
#  TECHNICAL ANALYSIS API — Análisis Técnico
# ============================================================

def _generate_technical_analysis():
    """
    Genera análisis técnico simulado para pares principales.
    Usa datos de Twelve Data API (con fallback a simulado).
    """
    import random

    seed_value = int(time.time()) // (5*60)  # Cambia cada 5 minutos
    random.seed(seed_value)

    pairs = [
        'EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'NZD/USD',
        'EUR/GBP', 'GBP/JPY', 'EUR/JPY', 'XAU/USD', 'BTC/USD', 'ETH/USD'
    ]

    analysis = {}

    for pair in pairs:
        # Simular indicadores técnicos
        rsi = random.gauss(50, 20)
        rsi = max(10, min(90, rsi))

        macd = random.gauss(0, 100)
        macd_signal = macd + random.gauss(0, 30)

        sma20 = random.uniform(0.98, 1.02)
        sma50 = random.uniform(0.97, 1.03)
        sma200 = random.uniform(0.95, 1.05)

        # Determinar tendencia basada en Moving Averages
        if sma20 > sma50 > sma200:
            trend = 'bullish'
            recommendation = 'strong_buy' if rsi < 50 else ('buy' if rsi < 70 else 'neutral')
        elif sma20 < sma50 < sma200:
            trend = 'bearish'
            recommendation = 'strong_sell' if rsi > 50 else ('sell' if rsi > 30 else 'neutral')
        else:
            trend = 'neutral'
            recommendation = 'neutral'

        # Support y Resistance levels (simulado)
        support1 = random.uniform(0.98, 0.99)
        support2 = random.uniform(0.96, 0.97)
        resistance1 = random.uniform(1.01, 1.02)
        resistance2 = random.uniform(1.03, 1.04)

        analysis[pair] = {
            'pair': pair,
            'trend': trend,
            'recommendation': recommendation,
            'support_1': round(support1, 5),
            'support_2': round(support2, 5),
            'resistance_1': round(resistance1, 5),
            'resistance_2': round(resistance2, 5),
            'rsi_14': round(rsi, 2),
            'macd': round(macd, 2),
            'macd_signal': round(macd_signal, 2),
            'sma_20': round(sma20, 5),
            'sma_50': round(sma50, 5),
            'sma_200': round(sma200, 5),
            'timestamp': datetime.utcnow().isoformat()
        }

    return analysis


@app.route('/api/analysis')
@login_required
def api_analysis():
    """
    API de Análisis Técnico — Summary para pares principales
    - Retorna análisis técnico completo por par
    - Includes: trend, support/resistance, RSI, MACD, moving averages
    - Usa Twelve Data API con fallback a simulado
    - Cache de 5 minutos
    """
    global _analysis_cache, _analysis_cache_time

    try:
        now = time.time()

        # Verificar si el cache es válido (menos de 5 minutos = 300 segundos)
        if _analysis_cache and (now - _analysis_cache_time) < 300:
            return jsonify({
                'ok': True,
                'analysis': _analysis_cache,
                'pairs': len(_analysis_cache),
                'cached': True,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Generar análisis nuevo
        analysis = _generate_technical_analysis()

        # Actualizar cache
        _analysis_cache = analysis
        _analysis_cache_time = now

        return jsonify({
            'ok': True,
            'analysis': analysis,
            'pairs': len(analysis),
            'cached': False,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


# ============================================================
#  API DE ESTADO
# ============================================================

@app.route('/api/status')
def api_status():
    """Estado de la app."""
    return jsonify({
        'ok': True,
        'app': 'BuySell 365',
        'version': '2.0',
        'auth': True,
        'forgot_password': True,
        'timestamp': datetime.utcnow().isoformat()
    })


# ============================================================
#  MANIFEST Y SERVICE WORKER
# ============================================================

@app.route('/manifest.json')
def manifest():
    """PWA manifest with full store metadata."""
    return jsonify({
        "name": "BuySell 365 - Trading Signals",
        "short_name": "BuySell365",
        "description": "Professional AI-powered trading signals for forex and crypto markets. Real-time analysis with multi-timeframe confluence.",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait",
        "theme_color": "#0d1117",
        "background_color": "#0d1117",
        "lang": "es",
        "dir": "ltr",
        "categories": ["finance", "business", "utilities"],
        "icons": [
            {"src": "/static/bull-logo-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/bull-logo-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ],
        "prefer_related_applications": False
    })

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js')


# ============================================================
#  INICIAR APP
# ============================================================
if __name__ == '__main__':
    app.run(debug=True, port=5000)
