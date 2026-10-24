"""Synthetic reproductions of signed-session invalidation; no real cookies."""
import unittest
from flask import Flask, session


def fixture(key):
    web = Flask(__name__)
    web.secret_key = key

    @web.post('/login')
    def login():
        session['authed'] = True
        session.permanent = True
        return {'ok': True}

    @web.get('/check')
    def check():
        return {'authed': bool(session.get('authed'))}

    return web


class AuthSessionTest(unittest.TestCase):
    def test_same_signing_key_preserves_login_across_restart(self):
        first = fixture('synthetic-first-key').test_client()
        first.post('/login')
        cookie = first.get_cookie('session').value
        second = fixture('synthetic-first-key').test_client()
        second.set_cookie('session', cookie)
        self.assertTrue(second.get('/check').json['authed'])

    def test_changed_signing_key_invalidates_existing_cookie(self):
        first = fixture('synthetic-first-key').test_client()
        first.post('/login')
        second = fixture('synthetic-other-key').test_client()
        second.set_cookie('session', first.get_cookie('session').value)
        self.assertFalse(second.get('/check').json['authed'])

    def test_default_cookie_collides_across_ports_on_same_host(self):
        first = fixture('synthetic-first-key')
        second = fixture('synthetic-other-key')
        self.assertEqual(first.config['SESSION_COOKIE_NAME'], second.config['SESSION_COOKIE_NAME'])
        client = first.test_client()
        client.post('/login', base_url='http://example.test:5050')
        self.assertTrue(client.get('/check', base_url='http://example.test:5051').json['authed'])
        other = second.test_client()
        other.post('/login', base_url='http://example.test:5051')
        client.set_cookie('session', other.get_cookie('session', domain='example.test').value, domain='example.test')
        self.assertFalse(client.get('/check', base_url='http://example.test:5050').json['authed'])
