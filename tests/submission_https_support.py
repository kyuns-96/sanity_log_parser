"""Explicit synthetic HTTPS fixtures, never imported by production entrypoints."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import os
import socket
import ssl
import threading
import time

from common.sanity_submission import VerifiedRemoteSubmitter, check
from vwp.submission_https import HttpsTransport

TOKEN = 'synthetic-submission-token-only'


class RemoteAuthority:
    def __init__(self, local):
        self.local = local
        self.actor = VerifiedRemoteSubmitter(**local.actor.actor)
        self.enabled = True

    def authenticate_bearer(self, token):
        check(self.enabled and token == TOKEN,'SUBMISSION_AUTH_REQUIRED')
        return self.actor

    def resolve_actor(self, actor):
        check(self.enabled and actor == self.actor.actor,'FORBIDDEN')
        return self.actor

    def authorize(self, actor, collection):
        check(self.enabled and actor.actor == self.actor.actor,'FORBIDDEN')
        return self.local.authorize(self.local.actor,collection)

    def approval_valid(self, plan):
        return self.local.approval_valid(plan)


def configs(root, actor):
    (root/'uploads').mkdir(mode=0o700,exist_ok=True)
    server = {'schema_version':2,'transport':'https','service_uid':os.geteuid(),
        'upload_root':str(root/'uploads'),'archive_root':str(root/'archive'),'node_id':'synthetic-https-node',
        'allowed_hosts':['testserver','localhost','127.0.0.1']}
    client = {'schema_version':2,'transport':'https','endpoint':'https://testserver',
        'ca_file':None,'timeout_seconds':5,'default_profile':'synthetic-top'}
    return client, server


class AsgiTransport(HttpsTransport):
    def __init__(self, config, http):
        super().__init__(config)
        self.http, self.calls = http, []

    def request(self, method, path, body=None, *, length=None, content_type='application/json'):
        from common.sanity_common import ReviewError
        self.calls.append((method,path))
        if hasattr(body,'read'):
            body = body.read()
        response = self.http.request(method,path,content=body,headers={
            'Authorization':'Bearer '+TOKEN,'Content-Type':content_type})
        if response.status_code not in (200,201,202):
            raise ReviewError(response.json().get('code','INVALID_SUBMISSION'),'synthetic transport failure')
        return response.json()


def certificate(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic HTTPS test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256()))
    certfile, keyfile = root/'synthetic-server.crt', root/'synthetic-server.key'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    keyfile.chmod(0o600)
    return certfile, keyfile


@contextmanager
def tls_server(app, root):
    import uvicorn
    certfile, keyfile = certificate(root)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile,keyfile)
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,ws='none',log_level='critical',
        proxy_headers=False,access_log=False,ssl_context_factory=lambda _config,_default:context))
    thread = threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:
                raise RuntimeError('Synthetic HTTPS server did not start')
            time.sleep(0.01)
        yield f'https://127.0.0.1:{port}', str(certfile)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError('Synthetic HTTPS server did not stop')
