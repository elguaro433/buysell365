"""
BuySell365 — COT Module (Commitments of Traders)
=================================================
Fuente: CFTC Public Reporting API (Socrata).
Endpoint oficial diseñado para acceso programático — NO bloquea bots.

Endpoint:  https://publicreporting.cftc.gov/resource/6dca-aqww.json
Docs:      https://publicreporting.cftc.gov/

Antes (deprecated): cftc.gov/dea/newcot/deafut.txt — bloqueaba con 403 Forbidden.
"""
import requests
import time
import logging

logger = logging.getLogger(__name__)

_cache_cot = {}
_COT_TTL = 86400        # 24h de caché (COT solo se publica los viernes)
_COT_ERROR_TTL = 1800   # Si falla: NO reintentar por 30min
_ultimo_error_cot = 0

# Códigos CFTC de los contratos seguidos
COT_CODES = {
    'GC=F':      '088691',   # Gold
    'EURUSD=X':  '099741',   # Euro FX
    'USDJPY=X':  '097741',   # Japanese Yen
    'BTC-USD':   '133741',   # Bitcoin
    'ES=F':      '13874A',   # S&P 500 E-mini
    'NQ=F':      '209742',   # Nasdaq-100 E-mini
}

# URL de la API Socrata (oficial, pública, sin API key)
_SOCRATA_URL = 'https://publicreporting.cftc.gov/resource/6dca-aqww.json'


def _parse_socrata(datos):
    """Parsea la respuesta JSON de Socrata y extrae los 6 contratos seguidos."""
    # Indexar por código de contrato para búsqueda rápida
    por_codigo = {}
    for fila in datos:
        code = (fila.get('cftc_contract_market_code') or '').strip()
        if code:
            # Si hay duplicados, quedarse con la fila más reciente (ya viene ordenado DESC)
            por_codigo.setdefault(code, fila)

    resultado = {}
    for ticker, cot_code in COT_CODES.items():
        fila = por_codigo.get(cot_code)
        if not fila:
            continue
        try:
            long_spec = float(fila.get('noncomm_positions_long_all', 0) or 0)
            short_spec = float(fila.get('noncomm_positions_short_all', 0) or 0)
            long_change = float(fila.get('change_in_noncomm_long_all', 0) or 0)
            short_change = float(fila.get('change_in_noncomm_short_all', 0) or 0)
            neta = long_spec - short_spec
            total = long_spec + short_spec
            ratio = long_spec / total if total > 0 else 0.5
            cambio_neto = long_change - short_change
            if ratio > 0.60:
                sesgo = 'LARGO'
            elif ratio < 0.40:
                sesgo = 'CORTO'
            else:
                sesgo = 'NEUTRO'
            resultado[ticker] = {
                'long': long_spec,
                'short': short_spec,
                'neta': neta,
                'ratio': round(ratio, 3),
                'cambio_neto': cambio_neto,
                'sesgo': sesgo,
                'fecha': (fila.get('report_date_as_yyyy_mm_dd') or '')[:10],
            }
        except (ValueError, TypeError):
            pass
    return resultado


def descargar_cot_cftc():
    """Descarga datos COT desde la API Socrata del CFTC.
    Retorna dict {ticker: {long, short, neta, ratio, sesgo, ...}, '_ts': timestamp}.
    """
    global _cache_cot, _ultimo_error_cot

    # 1. Caché válido → retornar directo
    if _cache_cot and (time.time() - _cache_cot.get('_ts', 0)) < _COT_TTL:
        return _cache_cot

    # 2. Error reciente → no reintentar (evita spam a la API)
    if _ultimo_error_cot and (time.time() - _ultimo_error_cot) < _COT_ERROR_TTL:
        return _cache_cot if _cache_cot else {}

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }

    try:
        # Descargar los últimos 500 registros (cubre todas las últimas publicaciones + activos)
        params = {
            '$limit': 500,
            '$order': 'report_date_as_yyyy_mm_dd DESC',
        }
        resp = requests.get(_SOCRATA_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        datos = resp.json()
        if not isinstance(datos, list) or len(datos) == 0:
            raise ValueError(f"Respuesta Socrata vacía o inesperada ({type(datos).__name__})")

        resultado = _parse_socrata(datos)
        if not resultado:
            raise ValueError("No se encontró ningún ticker de COT_CODES en la respuesta")

        resultado['_ts'] = time.time()
        _cache_cot = resultado
        _ultimo_error_cot = 0
        _fecha_rep = next(
            (v.get('fecha') for v in resultado.values() if isinstance(v, dict)),
            '?'
        )
        logger.info(f"COT actualizado via Socrata: {len(resultado) - 1} activos (reporte {_fecha_rep})")
        return resultado

    except Exception as e:
        _ultimo_error_cot = time.time()
        logger.warning(f"COT Socrata falló: {e} — usando caché previo, reintentará en {_COT_ERROR_TTL // 60} min")
        return _cache_cot if _cache_cot else {}


def obtener_sesgo_cot(ticker):
    datos = descargar_cot_cftc()
    if not datos or ticker not in datos:
        return None
    return datos[ticker]


def cot_confirma_senal(ticker, tipo_senal):
    cot = obtener_sesgo_cot(ticker)
    if cot is None:
        return True, "COT no disponible", 0.0
    sesgo = cot['sesgo']
    ratio = cot['ratio']
    cambio = cot['cambio_neto']
    if tipo_senal == 'COMPRA':
        if sesgo == 'LARGO' and cambio > 0:
            return True, f"COT CONFIRMA: Largos (ratio {ratio:.2f}, +{cambio:.0f})", 1.0
        elif sesgo == 'LARGO':
            return True, f"COT compatible: Largos (ratio {ratio:.2f})", 0.6
        elif sesgo == 'NEUTRO':
            return True, f"COT neutro ({ratio:.2f})", 0.0
        else:
            return False, f"COT EN CONTRA: Cortos ({ratio:.2f})", 0.0
    elif tipo_senal == 'VENTA':
        if sesgo == 'CORTO' and cambio < 0:
            return True, f"COT CONFIRMA: Cortos (ratio {ratio:.2f}, {cambio:.0f})", 1.0
        elif sesgo == 'CORTO':
            return True, f"COT compatible: Cortos (ratio {ratio:.2f})", 0.6
        elif sesgo == 'NEUTRO':
            return True, f"COT neutro ({ratio:.2f})", 0.3
        else:
            return False, f"COT EN CONTRA: Largos ({ratio:.2f})", 0.0
    return True, "COT sin datos", 0.0
