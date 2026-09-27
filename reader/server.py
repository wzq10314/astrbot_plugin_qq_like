"""Private, expiring reader for user-authorized non-explicit image ZIPs."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.parse import quote

MAX_ARCHIVE = 400 * 1024 * 1024
MAX_PAGE = 20 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_STORAGE = 5 * 1024 * 1024 * 1024
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}


def natural_key(name):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', name)]


def inspect_archive(path):
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if len(entries) > 5000:
            raise ValueError('压缩包条目过多')
        pages = []
        total = 0
        names = set()
        for i in entries:
            name = i.filename.replace('\\', '/')
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or name in names:
                raise ValueError('压缩包路径无效或重复')
            names.add(name)
            if i.is_dir() or p.suffix.lower() not in IMAGE_TYPES or '__MACOSX' in p.parts:
                continue
            if i.flag_bits & 1:
                raise ValueError('在线阅读暂不支持加密 ZIP，请关闭打包密码后重试')
            if i.file_size <= 0 or i.file_size > MAX_PAGE:
                raise ValueError('单页图片过大或为空')
            total += i.file_size
            if total > MAX_TOTAL:
                raise ValueError('解压后内容超过限制')
            # Validate raster signature without rendering, extracting or reading full images.
            with z.open(i) as f:
                head = f.read(16)
            valid = head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith((b'GIF87a', b'GIF89a')) or (head[:4] == b'RIFF' and head[8:12] == b'WEBP')
            if not valid:
                raise ValueError('压缩包含无法识别的图片')
            pages.append({'member': i.filename, 'name': p.name, 'chapter': str(p.parent) if str(p.parent) != '.' else '正文', 'mime': IMAGE_TYPES[p.suffix.lower()], 'size': i.file_size})
        if not pages or len(pages) > 3000:
            raise ValueError('压缩包没有图片，或页数超过 3000 页')
        return sorted(pages, key=lambda page: natural_key(page['member'].replace('\\', '/')))


class Library:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['state_dir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.source_root = Path(config['source_dir']).resolve()
        self.lock = threading.Lock()

    def cleanup(self):
        for folder in self.root.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}', folder.name) or not folder.is_dir() or folder.is_symlink():
                continue
            try:
                expires = json.loads((folder / 'manifest.json').read_text())['expires']
            except (OSError, ValueError, KeyError):
                expires = folder.stat().st_mtime + 3600
            if expires < time.time():
                shutil.rmtree(folder)

    def create(self, relative_path, title, source_name='comic', metadata=None):
        roots = self.config.get('source_dirs', {'comic': str(self.source_root)})
        if source_name not in roots:
            raise ValueError('未知来源')
        source_root = Path(roots[source_name]).resolve()
        rel = Path(relative_path)
        source = (source_root / rel).resolve()
        if rel.is_absolute() or not source.is_relative_to(source_root) or source.suffix.lower() != '.zip' or not source.is_file():
            raise ValueError('只允许已打包目录内的 ZIP')
        if source.stat().st_size > MAX_ARCHIVE:
            raise ValueError('压缩包超过 400 MB')
        with self.lock:
            self.cleanup()
            used = sum(p.stat().st_size for p in self.root.glob('*/book.zip') if p.is_file())
            if used + source.stat().st_size > MAX_STORAGE:
                raise ValueError('阅读服务存储空间已满，请清理过期记录')
            token = secrets.token_urlsafe(24)
            folder = self.root / hashlib.sha256(token.encode()).hexdigest()
            folder.mkdir(mode=0o700)
            try:
                shutil.copyfile(source, folder / 'book.zip')
                pages = inspect_archive(folder / 'book.zip')
                if source_name == 'pixiv':
                    details = (metadata or {}).get('images', {})
                    for page in pages:
                        item = details.get(page['member'], {})
                        for key in ['title', 'artist', 'pid', 'sha256']:
                            page[key] = str(item.get(key, ''))[:200]
                        for key in ['width', 'height']:
                            page[key] = max(0, int(item.get(key, 0)))
                expires = int(time.time()) + int(self.config.get('ttl_seconds', 604800))
                data = {'title': str(title)[:160], 'pages': pages, 'expires': expires, 'kind': source_name}
                if source_name == 'pixiv':
                    data['requested'] = max(len(pages), int((metadata or {}).get('requested', len(pages))))
                    data['failed'] = max(0, int((metadata or {}).get('failed', 0)))
                (folder / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False))
                return {'url': self.config['public_base'].rstrip('/') + '/r/' + token + '/', 'pages': len(pages), 'expires': expires}
            except BaseException:
                shutil.rmtree(folder)
                raise

    def get(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise FileNotFoundError()
        folder = self.root / hashlib.sha256(token.encode()).hexdigest()
        data = json.loads((folder / 'manifest.json').read_text())
        if data['expires'] < time.time():
            raise FileNotFoundError()
        return folder, data


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    def __init__(self, addr, library):
        self.library = library
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(addr, Handler)
    def process_request(self, request, address):
        if not self.slots.acquire(False):
            request.close()
            return
        request.settimeout(15)
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'Reader'
    sys_version = ''
    def log_message(self, *_):
        pass  # Access URLs are credentials; never log them.
    def reply(self, status, body, content_type='application/json; charset=utf-8', filename=None):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.response_headers(status, len(body), content_type, filename)
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    def response_headers(self, status, size, content_type, filename=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        if filename:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename, safe=''))
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            return self.reply(200, {'ok': True})
        m = re.fullmatch(r'/reader/r/([A-Za-z0-9_-]{32})/(.*)', path)
        if not m:
            return self.reply(404, {'error': '页面不存在或已过期'})
        token, resource = m.groups()
        try:
            folder, data = self.server.library.get(token)
            assets = {'': ('index.html', 'text/html; charset=utf-8'), 'app.js': ('app.js', 'application/javascript; charset=utf-8'), 'style.css': ('style.css', 'text/css; charset=utf-8')}
            if resource in assets:
                filename, mime = assets[resource]
                return self.reply(200, (Path(__file__).parent / 'web' / filename).read_bytes(), mime)
            if resource == 'originals.zip' and data.get('kind') == 'pixiv':
                archive = folder / 'book.zip'
                with archive.open('rb') as f:
                    self.response_headers(200, archive.stat().st_size, 'application/zip', 'pixiv-originals.zip')
                    if self.command != 'HEAD':
                        try:
                            shutil.copyfileobj(f, self.wfile, 65536)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                return
            if resource == 'manifest.json':
                fields = ['chapter', 'name', 'size', 'title', 'artist', 'pid', 'width', 'height', 'sha256']
                return self.reply(200, {'title': data['title'], 'expires': data['expires'], 'kind': data.get('kind', 'comic'), 'requested': data.get('requested', len(data['pages'])), 'failed': data.get('failed', 0), 'pages': [{k: p[k] for k in fields if k in p} for p in data['pages']]})
            if re.fullmatch(r'(?:page|original)/\d+', resource):
                index = int(resource.split('/')[1])
                if not 0 <= index < len(data['pages']):
                    raise FileNotFoundError()
                page = data['pages'][index]
                with zipfile.ZipFile(folder / 'book.zip') as z, z.open(page['member']) as f:
                    body = f.read(MAX_PAGE + 1)
                if len(body) != page['size'] or len(body) > MAX_PAGE:
                    raise ValueError()
                return self.reply(200, body, page['mime'], page['name'] if resource.startswith('original/') else None)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            pass
        self.reply(404, {'error': '页面不存在或已过期'})
    def do_POST(self):
        key = self.server.library.config['api_key']
        if self.path != '/api/collections' or not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + key).encode()):
            return self.reply(404, {'error': 'not found'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1048576:
                raise ValueError('请求长度无效')
            body = json.loads(self.rfile.read(length))
            result = self.server.library.create(body['path'], body.get('title', '漫画阅读'), body.get('source', 'comic'), body.get('metadata'))
            self.reply(201, result)
        except (ValueError, KeyError, zipfile.BadZipFile, OSError):
            self.reply(400, {'error': '无法生成阅读页：请检查 ZIP 是否未加密、包含有效图片且未超过容量限制。'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    library = Library(config)
    def clean_loop():
        while True:
            try:
                with library.lock:
                    library.cleanup()
            except OSError:
                pass
            time.sleep(1800)
    threading.Thread(target=clean_loop, daemon=True).start()
    Server((config.get('host', '127.0.0.1'), config.get('port', 8912)), library).serve_forever()


if __name__ == '__main__':
    main()
