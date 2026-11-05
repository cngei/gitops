#!/usr/bin/env python3
"""Authenticated, idempotent ESO write adapter for the local Bitwarden CLI API."""
import hmac
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler


class BridgeError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class VaultClient:
    MARKER = 'cngei-wordpress-external-secrets-v1'

    def __init__(self, origin, prefix, organization='', collections=None, folder=''):
        self.origin = origin.rstrip('/')
        self.prefix = prefix
        self.organization = organization or None
        self.collections = collections or []
        self.folder = folder or None
        self.http = build_opener(ProxyHandler({}))

    def call(self, method, path, body=None):
        raw = None if body is None else json.dumps(body).encode()
        req = Request(self.origin + path, data=raw, method=method,
                      headers={'Content-Type': 'application/json'})
        try:
            with self.http.open(req, timeout=25) as response:
                value = json.load(response)
            if value.get('success') is not True:
                raise BridgeError(502, 'Vaultwarden operation failed')
            return value.get('data')
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise BridgeError(502, 'Vaultwarden API unavailable') from exc

    def ready(self):
        status = self.call('GET', '/status')
        if not isinstance(status, dict):
            return False
        return status.get('status', status.get('template', {}).get('status')) == 'unlocked'

    def find(self, key):
        # Refresh the encrypted local cache before resolving names or updating.
        self.call('POST', '/sync', {})
        result = self.call('GET', '/list/object/items?' + urlencode({'search': key}))
        items = result.get('data', []) if isinstance(result, dict) else result
        if not isinstance(items, list):
            raise BridgeError(502, 'Invalid Vaultwarden response')
        matches = [item for item in items
                   if item.get('name') == self.prefix + key
                   and (item.get('organizationId') or None) == self.organization
                   and not item.get('deletedDate')]
        if len(matches) > 1:
            raise BridgeError(409, 'Duplicate Vaultwarden item names; resolve manually')
        if not matches:
            return None, None
        item = matches[0]
        try:
            notes = json.loads(item['notes'])
            valid = notes.get('managedBy') == self.MARKER and notes.get('key') == key
            data = notes['data']
        except (KeyError, TypeError, ValueError):
            valid, data = False, None
        if not valid or not isinstance(data, dict):
            raise BridgeError(409, 'Existing Vaultwarden item is not managed by this bridge')
        return item, data

    def upsert(self, key, data, existing):
        item = dict(existing or {})
        item.update({
            'type': 1,
            'name': self.prefix + key,
            'organizationId': self.organization,
            'collectionIds': self.collections,
            'folderId': self.folder,
            'notes': json.dumps({'managedBy': self.MARKER, 'key': key, 'data': data},
                               ensure_ascii=False, sort_keys=True),
            'login': {
                'username': data['WORDPRESS_ADMIN_USER'],
                'password': data['WORDPRESS_ADMIN_PASSWORD'],
                'totp': None,
                'uris': [{'match': None, 'uri': data['WORDPRESS_SITE_URL'] + '/wp-admin/'}],
            },
            'fields': [
                {'name': name, 'value': data[name], 'type': 1 if 'PASSWORD' in name else 0}
                for name in ['WORDPRESS_ADMIN_EMAIL', 'WORDPRESS_DB_HOST', 'WORDPRESS_DB_NAME',
                             'WORDPRESS_DB_USER', 'WORDPRESS_DB_PASSWORD']
            ],
        })
        if existing:
            self.call('PUT', '/object/item/' + existing['id'], item)
        else:
            self.call('POST', '/object/item', item)


class Bridge:
    REQUIRED = ['WORDPRESS_ADMIN_USER', 'WORDPRESS_ADMIN_PASSWORD', 'WORDPRESS_ADMIN_EMAIL',
                'WORDPRESS_DB_HOST', 'WORDPRESS_DB_NAME', 'WORDPRESS_DB_USER',
                'WORDPRESS_DB_PASSWORD', 'WORDPRESS_SITE_URL']

    def __init__(self, client, token, allowed_keys):
        if len(token) < 32:
            raise ValueError('Webhook token must contain at least 32 characters')
        self.client = client
        self.token = token
        self.allowed_keys = set(allowed_keys)
        self.lock = threading.Lock()

    def request(self, authorization, key, data):
        if not hmac.compare_digest(authorization.encode(), ('Bearer ' + self.token).encode()):
            raise BridgeError(401, 'Unauthorized')
        if key not in self.allowed_keys:
            raise BridgeError(403, 'Key is outside this deployment')
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise BridgeError(400, 'Expected a JSON string map')
        if data and (any(not data.get(k) for k in self.REQUIRED)
                     or data.get('WORDPRESS_SITE_URL') != 'https://' + key):
            raise BridgeError(400, 'Invalid WordPress Secret data')
        with self.lock:
            existing, previous = self.client.find(key)
            # ESO may perform an existence check against the same store URL.
            if not data:
                if existing is None:
                    raise BridgeError(404, 'Not found')
                return previous
            if previous != data:
                self.client.upsert(key, data, existing)
            return {'saved': True}


def handler_for(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # Do not log selectors, request/response bodies, headers or credentials.
            pass

        def send(self, status, value):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/healthz':
                return self.send(200, {'ok': True})
            if self.path == '/readyz':
                try:
                    ready = bridge.client.ready()
                except Exception:
                    ready = False
                return self.send(200 if ready else 503, {'ready': ready})
            self.send(404, {'error': 'Not found'})

        def do_POST(self):
            try:
                self.connection.settimeout(15)
                uri = urlsplit(self.path)
                if uri.path != '/items':
                    raise BridgeError(404, 'Not found')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 131072:
                    raise BridgeError(413, 'Invalid body length')
                data = json.loads(self.rfile.read(size))
                keys = parse_qs(uri.query).get('key', [])
                if len(keys) != 1:
                    raise BridgeError(400, 'Expected one key')
                result = bridge.request(self.headers.get('Authorization', ''), keys[0], data)
                self.send(200, {'data': result})
            except BridgeError as exc:
                self.send(exc.status, {'error': exc.message})
            except (ValueError, UnicodeError):
                self.send(400, {'error': 'Invalid JSON body'})
            except Exception:
                self.send(502, {'error': 'Vaultwarden bridge operation failed'})
    return Handler


if __name__ == '__main__':
    client = VaultClient('http://127.0.0.1:8087', os.environ['ITEM_NAME_PREFIX'],
                         os.getenv('ORGANIZATION_ID', ''),
                         json.loads(os.getenv('COLLECTION_IDS', '[]')),
                         os.getenv('FOLDER_ID', ''))
    bridge = Bridge(client, Path('/auth/token').read_text().strip(),
                    json.loads(os.environ['ALLOWED_KEYS']))
    ThreadingHTTPServer(('0.0.0.0', 8080), handler_for(bridge)).serve_forever()
