"""T19 shifted features. None denotes missing; no target or future label filling."""
from datetime import date,timedelta
import math
import numpy as np

VERSION='inventraiq-shifted-features-v1'
LAGS=(1,7,14,28)
NAMES=('day_index','weekday_sin','weekday_cos','lag1','lag7','lag14','lag28',
       'mean7','mean28','observed7','observed28')


def row(values,index,start):
    """Only indices < index are inspected, even if future values are supplied."""
    day=date.fromisoformat(start)+timedelta(days=index)
    result=[float(index),math.sin(2*math.pi*day.weekday()/7),math.cos(2*math.pi*day.weekday()/7)]
    result += [float(values[index-lag]) if index>=lag and values[index-lag] is not None else np.nan for lag in LAGS]
    windows=[[v for v in values[max(0,index-n):index] if v is not None] for n in (7,28)]
    result += [sum(w)/len(w) if w else np.nan for w in windows]
    return result+[float(len(w)) for w in windows]


def training(values,start):
    indices=[i for i,v in enumerate(values) if i>=28 and v is not None]
    return np.asarray([row(values,i,start) for i in indices],dtype=float),np.asarray([values[i] for i in indices],dtype=float)
