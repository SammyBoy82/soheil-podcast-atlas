#!/usr/bin/env python3
import json, re, html, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
UA = 'Mozilla/5.0 (compatible; SoheilPodcastAtlas/7.0; +https://github.com/SammyBoy82/soheil-podcast-atlas)'

def get(url, timeout=15):
    req = Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def clean_text(s):
    if not s: return ''
    s = html.unescape(re.sub(r'<[^>]+>', ' ', str(s)))
    return re.sub(r'\s+', ' ', s).strip()

def norm(s):
    s = clean_text(s).lower()
    s = re.sub(r'[^\w\u0600-\u06ff]+', ' ', s, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', s).strip()

def score(a,b):
    a,b=norm(a),norm(b)
    if not a or not b: return 0
    if a==b: return 1
    if a in b or b in a: return .9
    A={x for x in a.split() if len(x)>1}; B={x for x in b.split() if len(x)>1}
    return len(A&B)/max(len(A),len(B),1)

def load_podcasts():
    items=[]
    for path in sorted(ROOT.glob('data-*.js')):
        txt=path.read_text(encoding='utf-8')
        a=txt.find('['); b=txt.rfind(']')
        if a<0 or b<a: continue
        try: items.extend(json.loads(txt[a:b+1]))
        except Exception as e: print('parse',path.name,e)
    return items

def page_meta(url):
    try:
        text=get(url).decode('utf-8','ignore')
        def meta(prop):
            pats=[
              rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
              rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']'
            ]
            for p in pats:
                m=re.search(p,text,re.I)
                if m:return html.unescape(m.group(1)).strip()
            return ''
        return {'image':meta('og:image') or meta('twitter:image'), 'description':clean_text(meta('og:description') or meta('description'))}
    except Exception as e:
        return {}

def apple_search(title,country):
    try:
        url=f'https://itunes.apple.com/search?media=podcast&entity=podcast&limit=15&country={country}&term={quote(title)}'
        data=json.loads(get(url).decode('utf-8','ignore'))
        best=None; bestscore=0
        for r in data.get('results',[]):
            s=score(title,r.get('collectionName') or r.get('trackName') or '')
            if s>bestscore:bestscore=s;best=r
        if best and bestscore>=.22:
            art=best.get('artworkUrl600') or best.get('artworkUrl100') or best.get('artworkUrl60') or ''
            art=re.sub(r'/\d+x\d+bb', '/1200x1200bb', art)
            return {'image':art,'feedUrl':best.get('feedUrl',''),'appleUrl':best.get('collectionViewUrl',''),'matched':best.get('collectionName',''),'score':bestscore}
    except Exception:
        pass
    return {}

def rss_meta(feed):
    if not feed:return {}
    try:
        raw=get(feed,20)
        root=ET.fromstring(raw)
        ch=root.find('channel')
        if ch is None:return {}
        desc=''; image=''
        for tag in ['{http://www.itunes.com/dtds/podcast-1.0.dtd}summary','description']:
            el=ch.find(tag)
            if el is not None and clean_text(el.text): desc=clean_text(el.text); break
        itimg=ch.find('{http://www.itunes.com/dtds/podcast-1.0.dtd}image')
        if itimg is not None:image=itimg.attrib.get('href','')
        if not image:
            el=ch.find('image/url')
            if el is not None:image=(el.text or '').strip()
        return {'image':image,'description':desc,'sourceUrl':feed}
    except Exception:
        return {}

def resolve(p):
    out={'title':p.get('title',''),'sourcePage':p.get('url','')}
    pm=page_meta(p.get('url','')) if p.get('url','').startswith('http') else {}
    ap=apple_search(p.get('title',''),'au') or apple_search(p.get('title',''),'us')
    rm=rss_meta(ap.get('feedUrl','')) if ap else {}
    # Prefer publisher RSS metadata, then catalogue page, then Apple artwork.
    image=rm.get('image') or pm.get('image') or ap.get('image','')
    desc=rm.get('description') or pm.get('description') or p.get('desc','')
    source_name='Publisher RSS' if rm.get('description') or rm.get('image') else ('Podcast App' if pm else ('Apple Podcasts' if ap else 'Atlas'))
    source_url=rm.get('sourceUrl') or p.get('url','') or ap.get('appleUrl','')
    out.update({'image':image,'description':desc,'sourceName':source_name,'sourceUrl':source_url,'appleUrl':ap.get('appleUrl','') if ap else '', 'feedUrl':ap.get('feedUrl','') if ap else ''})
    print(f"{p.get('id')} {p.get('title')[:42]} -> {source_name} image={'yes' if image else 'no'} desc={len(desc)}")
    return p.get('id'),out

def main():
    pods=load_podcasts(); meta={}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs=[ex.submit(resolve,p) for p in pods]
        for f in as_completed(futs):
            try:
                k,v=f.result(); meta[k]=v
            except Exception as e: print('resolver error',e)
    out='window.PODCAST_META = '+json.dumps(meta,ensure_ascii=False,separators=(',',':'))+';\n'
    (ROOT/'source-metadata.js').write_text(out,encoding='utf-8')
    print('wrote source-metadata.js',len(meta),'shows')

if __name__=='__main__': main()
