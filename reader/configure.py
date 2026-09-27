"""Generate private reader configuration; no credentials are printed."""
import argparse,json,os,secrets
from pathlib import Path
from urllib.parse import urlsplit

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--public-base',required=True,help='https://your-domain/reader')
    args=parser.parse_args()
    base=args.public_base.rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path!='/reader':
        parser.error('Use https://your-domain/reader')
    root=Path(__file__).resolve().parent
    if any((root/name).exists() for name in ['config.json','reader-client.json']):
        parser.error('Config already exists; edit it in place to preserve the shared key')
    os.umask(0o077)
    key=secrets.token_urlsafe(36)
    config={'host':'0.0.0.0','port':8912,'source_dir':'/source/pica/packs',
            'source_dirs':{'comic':'/source/pica/packs','pixiv':'/source/pixiv_reborn/original-gallery-staging'},
            'state_dir':'/state','public_base':base,'api_key':key,'ttl_seconds':604800}
    client={'enabled':True,'endpoint':'http://qq-like-reader:8912/api/collections','public_base':base,'api_key':key}
    for name,data in [('config.json',config),('reader-client.json',client)]:
        p=root/name
        with p.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        p.chmod(0o600)
    (root/'state').mkdir(mode=0o700,exist_ok=True)
    print('Created private config.json and reader-client.json. Keep both out of Git.')

if __name__=='__main__':main()
