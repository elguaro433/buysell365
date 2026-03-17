"""Wrapper para reemplazar pandas_ta con la libreria ta."""
import ta as _ta
import pandas as pd

def ema(close, length=20):
    return _ta.trend.ema_indicator(close, window=length)

def sma(close, length=20):
    return _ta.trend.sma_indicator(close, window=length)

def rsi(close, length=14):
    return _ta.momentum.rsi(close, window=length)

def macd(close, fast=12, slow=26, signal=9):
    m = _ta.trend.MACD(close, window_slow=slow, window_fast=fast, window_sign=signal)
    df = pd.DataFrame()
    df['MACD_12_26_9'] = m.macd()
    df['MACDs_12_26_9'] = m.macd_signal()
    df['MACDh_12_26_9'] = m.macd_diff()
    return df

def bbands(close, length=20, std=2):
    bb = _ta.volatility.BollingerBands(close, window=length, window_dev=std)
    df = pd.DataFrame()
    df['BBL_20_2.0'] = bb.bollinger_lband()
    df['BBM_20_2.0'] = bb.bollinger_mavg()
    df['BBU_20_2.0'] = bb.bollinger_hband()
    return df

def atr(high, low, close, length=14):
    return _ta.volatility.average_true_range(high, low, close, window=length)

def stoch(high, low, close, k=14, d=3):
    s = _ta.momentum.StochasticOscillator(high, low, close, window=k, smooth_window=d)
    df = pd.DataFrame()
    df['STOCHk_14_3_3'] = s.stoch()
    df['STOCHd_14_3_3'] = s.stoch_signal()
    return df

def adx(high, low, close, length=14):
    adx_val = _ta.trend.adx(high, low, close, window=length)
    plus_di = _ta.trend.adx_pos(high, low, close, window=length)
    minus_di = _ta.trend.adx_neg(high, low, close, window=length)
    df = pd.DataFrame()
    df[f'ADX_{length}'] = adx_val
    df[f'DMP_{length}'] = plus_di
    df[f'DMN_{length}'] = minus_di
    return df
