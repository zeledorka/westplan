"""Typed JSON checkpoint codec preserving opaque Gemini byte signatures without collisions."""
import base64


def pack(value):
    if isinstance(value,bytes):return ['bytes',base64.b64encode(value).decode('ascii')]
    if isinstance(value,dict):return ['dict',[[k,pack(v)] for k,v in value.items()]]
    if isinstance(value,(list,tuple)):return ['list',[pack(v) for v in value]]
    if value is None or isinstance(value,(str,int,float,bool)):return ['value',value]
    raise ValueError('Unsupported checkpoint value')


def unpack(value):
    kind,data=value
    if kind=='bytes':return base64.b64decode(data,validate=True)
    if kind=='dict':return {k:unpack(v) for k,v in data}
    if kind=='list':return [unpack(v) for v in data]
    if kind=='value':return data
    raise ValueError('Unsupported checkpoint encoding')
