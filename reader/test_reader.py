import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import hashlib
import unittest
import zipfile
from server import Library, Server, inspect_archive

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhRsAAAAASUVORK5CYII=')

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.packs = root/'packs'; self.packs.mkdir()
        self.lib = Library({'source_dir':str(self.packs),'state_dir':str(root/'state'),'public_base':'https://example.test/reader','api_key':'unit-secret','ttl_seconds':604800})
        self.zip = self.packs/'book.zip'
        with zipfile.ZipFile(self.zip,'w') as z:
            for name in ['ep10/1.png','ep2/10.png','ep2/2.png']:
                z.writestr(name,PNG)
    def tearDown(self):
        self.temp.cleanup()
    def test_natural_chapter_and_page_order(self):
        self.assertEqual([p['member'] for p in inspect_archive(self.zip)],['ep2/2.png','ep2/10.png','ep10/1.png'])
    def test_snapshot_does_not_follow_overwrite(self):
        result=self.lib.create('book.zip','Demo')
        folder,data=self.lib.get(result['url'].split('/')[-2])
        self.zip.write_bytes(b'overwritten')
        self.assertEqual(len(inspect_archive(folder/'book.zip')),3)
    def test_outside_paths_rejected(self):
        for path in ['../secret.zip',str(self.zip)]:
            with self.assertRaises(ValueError):self.lib.create(path,'bad')
    def test_zip_traversal_rejected(self):
        with zipfile.ZipFile(self.zip,'w') as z:z.writestr('../evil.png',PNG)
        with self.assertRaises(ValueError):self.lib.create('book.zip','bad')
    def test_invalid_image_rejected(self):
        with zipfile.ZipFile(self.zip,'w') as z:z.writestr('evil.png',b'<script>bad</script>')
        with self.assertRaises(ValueError):self.lib.create('book.zip','bad')
    def test_expired_link_and_cleanup(self):
        r=self.lib.create('book.zip','Demo');token=r['url'].split('/')[-2]
        folder,data=self.lib.get(token);data['expires']=time.time()-1
        (folder/'manifest.json').write_text(json.dumps(data))
        with self.assertRaises(FileNotFoundError):self.lib.get(token)
        self.lib.cleanup();self.assertFalse(folder.exists());self.assertTrue(self.zip.exists())
    def test_http_contract(self):
        server=Server(('127.0.0.1',0),self.lib)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        def call(method,path,body=None,key=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Authorization':'Bearer '+key} if key else {}
            c.request(method,path,json.dumps(body) if body else None,headers)
            r=c.getresponse();data=r.read();status=r.status;hdr=dict(r.getheaders());c.close()
            return status,data,hdr
        try:
            self.assertEqual(call('POST','/api/collections',{'path':'book.zip'})[0],404)
            status,body,_=call('POST','/api/collections',{'path':'book.zip','title':'<unsafe>'},'unit-secret')
            self.assertEqual(status,201)
            prefix='/reader/r/'+json.loads(body)['url'].split('/')[-2]+'/'
            status,body,hdr=call('GET',prefix+'manifest.json')
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['title'],'<unsafe>')
            self.assertNotIn('member',json.loads(body)['pages'][0]);self.assertEqual(hdr['Referrer-Policy'],'no-referrer')
            self.assertEqual(call('GET',prefix+'page/0')[1],PNG)
            self.assertEqual(call('GET',prefix+'page/99')[0],404)
            self.assertEqual(call('GET','/reader/')[0],404)
            self.assertEqual(call('GET',prefix)[0],200)
            self.assertEqual(call('HEAD',prefix+'page/0')[1],b'')
        finally:server.shutdown();server.server_close();t.join()
    def test_original_download_bytes_unchanged(self):
        self.lib.config['source_dirs']={'comic':str(self.packs),'pixiv':str(self.packs)}
        original_hash=hashlib.sha256(PNG).hexdigest()
        result=self.lib.create('book.zip','Originals','pixiv',{'images':{'ep2/2.png':{'width':1,'height':1,'pid':'123','sha256':original_hash}},'requested':4,'failed':1})
        token=result['url'].split('/')[-2]
        server=Server(('127.0.0.1',0),self.lib)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            for suffix in ['page/0','original/0']:
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                c.request('GET','/reader/r/'+token+'/'+suffix);r=c.getresponse()
                self.assertEqual(r.status,200);self.assertEqual(hashlib.sha256(r.read()).hexdigest(),original_hash)
                if suffix.startswith('original'):self.assertIn('attachment',r.getheader('Content-Disposition'))
                c.close()
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            c.request('GET','/reader/r/'+token+'/originals.zip');r=c.getresponse()
            self.assertEqual(r.read(),self.zip.read_bytes());c.close()
        finally:server.shutdown();server.server_close();t.join()

if __name__=='__main__':unittest.main()
