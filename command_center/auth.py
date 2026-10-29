"""Scoped browser sessions; tokens stay out of WebSocket URLs and storage."""
from __future__ import annotations
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time


def is_loopback(host):
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return host=='localhost'


class SessionAuth:
    def __init__(self,host='127.0.0.1',operator_token=None,viewer_token=None,local_development=True,ttl=3600):
        self.operator_token=operator_token or os.environ.get('AETHERTWIN_OPERATOR_TOKEN')
        self.viewer_token=viewer_token or os.environ.get('AETHERTWIN_VIEWER_TOKEN')
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

    def role(self,cookie=None,peer=None):
        if self.local_development and peer and is_loopback(peer):return 'operator'
        if not cookie or len(cookie)>2048:return None
        try:
            payload,signature=cookie.encode().split(b'.')
            if not hmac.compare_digest(hmac.new(self.key,payload,hashlib.sha256).hexdigest().encode(),signature):return None
            data=json.loads(base64.urlsafe_b64decode(payload+b'='*((-len(payload))%4)))
            if data['expires']<time.time() or data['role'] not in ('operator','viewer'):return None
            return data['role']
        except (ValueError,KeyError,TypeError):return None
