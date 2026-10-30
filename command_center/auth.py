"""Scoped browser sessions; tokens stay out of WebSocket URLs and storage."""
from __future__ import annotations
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time

AUDIT=logging.getLogger('aethertwin.audit')


def audit(event,**fields):
    """One JSON line per security-relevant event. Callers never pass credentials."""
    AUDIT.info(json.dumps({'event':event,**fields},separators=(',',':'),default=str))


def configure_audit_log():
    """Entry points call this: uvicorn configures only its own loggers, so audit lines need a handler."""
    if AUDIT.handlers:return
    handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('%(asctime)s %(name)s %(message)s'))
    AUDIT.addHandler(handler);AUDIT.setLevel(logging.INFO);AUDIT.propagate=False


def configured_token(explicit,name):
    """A credential from the argument, NAME, or the file named by NAME_FILE (Compose/Docker secrets)."""
    if explicit:return explicit
    value=os.environ.get(name);path=os.environ.get(name+'_FILE')
    if value and path:raise ValueError(f'Set {name} or {name}_FILE, not both')
    if not path:return value
    data=Path(path).read_bytes()
    if len(data)>4096:raise ValueError(f'{name}_FILE is larger than 4 KiB')
    return data.decode('utf-8').strip() or None


class LoginLimiter:
    """Per-peer token bucket for failed sign-ins: `burst` failures, refilled at `rate` per second.

    Memory is bounded: the least recently failing peers are forgotten first."""
    def __init__(self,burst=5,rate=5/60,max_peers=4096,clock=time.monotonic):
        self.burst=float(burst);self.rate=float(rate);self.max_peers=int(max_peers);self.clock=clock
        self.buckets={};self.lock=threading.Lock()
    def _tokens(self,peer,now):
        tokens,stamp=self.buckets.get(peer,(self.burst,now));return min(self.burst,tokens+(now-stamp)*self.rate)
    def retry_after(self,peer):
        """Seconds until this peer may try again; 0 when an attempt is allowed."""
        with self.lock:
            tokens=self._tokens(peer,self.clock());return 0. if tokens>=1 else (1-tokens)/self.rate
    def acquire(self,peer):
        """Take one attempt atomically, so parallel requests cannot pass the burst; returns seconds to wait (0 = taken)."""
        with self.lock:
            now=self.clock();tokens=self._tokens(peer,now)
            if tokens<1:return (1-tokens)/self.rate
            self.buckets.pop(peer,None);self.buckets[peer]=(tokens-1,now)
            while len(self.buckets)>self.max_peers:self.buckets.pop(next(iter(self.buckets)))
            return 0.
    def failure(self,peer):
        with self.lock:
            now=self.clock();tokens=max(0.,self._tokens(peer,now)-1);self.buckets.pop(peer,None);self.buckets[peer]=(tokens,now)
            while len(self.buckets)>self.max_peers:self.buckets.pop(next(iter(self.buckets)))
    def success(self,peer):
        """A valid credential clears the peer's failures (and refunds the attempt)."""
        with self.lock:self.buckets.pop(peer,None)


def is_loopback(host):
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return host=='localhost'


class SessionAuth:
    def __init__(self,host='127.0.0.1',operator_token=None,viewer_token=None,local_development=True,ttl=3600):
        self.operator_token=configured_token(operator_token,'AETHERTWIN_OPERATOR_TOKEN')
        self.viewer_token=configured_token(viewer_token,'AETHERTWIN_VIEWER_TOKEN')
        for token in (self.operator_token,self.viewer_token):
            if token is not None and len(token)<32:raise ValueError('Access tokens must contain at least 32 characters')
        if self.operator_token and self.operator_token==self.viewer_token:
            raise ValueError('Viewer and operator credentials must be different')
        self.local_development=bool(local_development and is_loopback(host) and not (self.operator_token or self.viewer_token))
        if not is_loopback(host) and not self.operator_token:raise ValueError('Network exposure requires AETHERTWIN_OPERATOR_TOKEN')
        self.key=secrets.token_bytes(32);self.ttl=int(ttl)
        if self.ttl<1:raise ValueError('Invalid session lifetime')

    def create(self,token):
        role=None
        if self.operator_token and isinstance(token,str) and hmac.compare_digest(token.encode(),self.operator_token.encode()):role='operator'
        elif self.viewer_token and isinstance(token,str) and hmac.compare_digest(token.encode(),self.viewer_token.encode()):role='viewer'
        if role is None:raise ValueError('Invalid access credential')
        payload=base64.urlsafe_b64encode(json.dumps({'role':role,'expires':time.time()+self.ttl,'nonce':secrets.token_hex(8)},separators=(',',':')).encode()).rstrip(b'=')
        signature=hmac.new(self.key,payload,hashlib.sha256).hexdigest().encode()
        return (payload+b'.'+signature).decode(),role

    def role(self,cookie=None,peer=None):return self.session(cookie,peer)[0]

    def session(self,cookie=None,peer=None):
        """(role, expiry as Unix time or None for loopback development); (None, None) when not signed in."""
        if self.local_development and peer and is_loopback(peer):return 'operator',None
        if not cookie or len(cookie)>2048:return None,None
        try:
            payload,signature=cookie.encode().split(b'.')
            if not hmac.compare_digest(hmac.new(self.key,payload,hashlib.sha256).hexdigest().encode(),signature):return None,None
            data=json.loads(base64.urlsafe_b64decode(payload+b'='*((-len(payload))%4)))
            if data['expires']<time.time() or data['role'] not in ('operator','viewer'):return None,None
            return data['role'],float(data['expires'])
        except (ValueError,KeyError,TypeError):return None,None
