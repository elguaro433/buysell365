import requests
import time
import logging
import ssl
import urllib.request

logger = logging.getLogger(__name__)

_cache_cot = {}
_COT_TTL = 86400        # Cache válido 24 horas
_COT_ERROR_TTL = 1800   # Si falla, NO reintentar por 30 minutos (evita martillear CFTC)
_ultimo_error_cot = 0   # Timestamp del último fallo

COT_CODES = {
    'GC=F': '088691',
    'EURUSD=X': '099741',
    'USDJPY=X': '097741',
    'BTC-USD': '133741',
    'ES=F': '13874A',
    'NQ=F': '209742',
}

def descargar_cot_cftc():
    """Descarga datos COT de la CFTC (Commitments of Traders).
    El archivo deafut.txt NO tiene headers — se parsea por posición de columna.
    Col 3 = CFTC_Contract_Market_Code
    Col 8 = NonComm_Positions_Long_All
    Col 9 = NonComm_Positions_Short_All
    Col 15/16 = Change_in_NonComm_Long/Short (aprox)
    """
    global _cache_cot, _ultimo_error_cot

    # 1. Cache válido → retornar directo
    if _cache_cot and (time.time() - _cache_cot.get('_ts', 0)) < _COT_TTL:
        return _cache_cot

    # 2. Error reciente → NO reintentar (evita martillear CFTC con 403)
    if _ultimo_error_cot and (time.time() - _ultimo_error_cot) < _COT_ERROR_TTL:
        return _cache_cot if _cache_cot else {}

    try:
        url = 'https://www.cftc.gov/dea/newcot/deafut.txt'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        text = None
        # Intento 1: requests con SSL válido (correcto)
        try:
            resp = requests.get(url, headers=headers, timeout=30, verify=True)
            resp.raise_for_status()
            text = resp.text
        except Exception as e1:
            logger.debug(f"COT SSL normal falló ({e1}), probando sin verificación...")
            # Intento 2: requests sin SSL — fallback solo si el certificado de CFTC falla
            try:
                resp = requests.get(url, headers=headers, timeout=30, verify=False)
                resp.raise_for_status()
                text = resp.text
            except Exception as e2:
                logger.debug(f"COT requests falló ({e2}), probando urllib...")
                # Intento 3: urllib como último recurso
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.Request(url, headers=headers)
                    resp3 = urllib.request.urlopen(req, context=ctx, timeout=30)
                    text = resp3.read().decode('utf-8')
                except Exception as e3:
                    logger.debug(f"COT urllib también falló: {e3}")
                    raise e3

        if not text or len(text) < 1000:
            raise ValueError(f"COT respuesta vacía o muy corta ({len(text) if text else 0} bytes)")

        resultado = {}
        for line in text.strip().split('\n'):
            cols = line.split(',')
            if len(cols) < 20:
                continue
            code = cols[3].strip()
            for ticker, cot_code in COT_CODES.items():
                if code == cot_code:
                    try:
                        long_spec = float(cols[8].strip())
                        short_spec = float(cols[9].strip())
                        neta = long_spec - short_spec
                        total = long_spec + short_spec
                        ratio = long_spec / total if total > 0 else 0.5
                        long_change = float(cols[15].strip()) if len(cols) >= 16 else 0
                        short_change = float(cols[16].strip()) if len(cols) >= 17 else 0
                        cambio_neto = long_change - short_change
                        if ratio > 0.60:
                            sesgo = 'LARGO'
                        elif ratio < 0.40:
                            sesgo = 'CORTO'
                        else:
                            sesgo = 'NEUTRO'
                        resultado[ticker] = {
                            'long': long_spec, 'short': short_spec,
                            'neta': neta, 'ratio': round(ratio, 3),
                            'cambio_neto': cambio_neto, 'sesgo': sesgo,
                        }
                    except (ValueError, IndexError):
                        pass
                    break

        resultado['_ts'] = time.time()
        _cache_cot = resultado
        _ultimo_error_cot = 0  # Reset error timer on success
        logger.info(f"COT actualizado: {len(resultado)-1} activos")
        return resultado

    except Exception as e:
        _ultimo_error_cot = time.time()  # Marcar fallo — no reintentar por 30 min
        logger.info(f"Error COT: {e} — no se reintentará por {_COT_ERROR_TTL//60} min")
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
