#!/usr/bin/env python3
import json, re, html, mimetypes
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote, urljoin
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
COVERS = ROOT / 'covers'
COVERS.mkdir(exist_ok=True)
UA = 'Mozilla/5.0 (compatible; SoheilPodcastAtlas/8.0; +https://github.com/SammyBoy82/soheil-podcast-atlas)'


def request(url, timeout=18, referer=''):
    headers = {
        'User-Agent': UA,
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'en-AU,en;q=0.9,fa;q=0.8',
    }
    if referer:
        headers['Referer'] = referer
    req = Request(url, headers=headers)
    return urlopen(req, timeout=timeout)


def get(url, timeout=18, referer=''):
    with request(url, timeout, referer) as r:
        return r.read(), dict(r.headers), r.geturl()


def clean_text(s):
    if not s:
        return ''
    s = html.unescape(re.sub(r'<[^>]+>', ' ', str(s)))
    return re.sub(r'\s+', ' ', s).strip()


def norm(s):
    s = clean_text(s).lower()
    s = re.sub(r'[^\w\u0600-\u06ff]+', ' ', s, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', s).strip()


def score(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a == b:
        return 1
    if a in b or b in a:
        return .9
    A = {x for x in a.split() if len(x) > 1}
    B = {x for x in b.split() if len(x) > 1}
    return len(A & B) / max(len(A), len(B), 1)


def load_podcasts():
    items = []
    for path in sorted(ROOT.glob('data-*.js')):
        txt = path.read_text(encoding='utf-8')
        a, b = txt.find('['), txt.rfind(']')
        if a < 0 or b < a:
            continue
        try:
            items.extend(json.loads(txt[a:b + 1]))
        except Exception as e:
            print('parse', path.name, e)
    return items


def meta_value(text, prop):
    pats = [
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ''


def page_meta(url):
    try:
        raw, _, final_url = get(url)
        text = raw.decode('utf-8', 'ignore')
        image = meta_value(text, 'og:image') or meta_value(text, 'twitter:image')
        desc = clean_text(meta_value(text, 'og:description') or meta_value(text, 'description'))
        if image:
            image = urljoin(final_url, image)
        # JSON-LD fallback: many podcast directories expose image/description here.
        if not image or not desc:
            for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
                try:
                    obj = json.loads(html.unescape(block).strip())
                    objs = obj if isinstance(obj, list) else [obj]
                    for x in objs:
                        if not isinstance(x, dict):
                            continue
                        if not image:
                            im = x.get('image') or x.get('thumbnailUrl')
                            if isinstance(im, dict):
                                im = im.get('url')
                            if isinstance(im, list) and im:
                                im = im[0]
                            if im:
                                image = urljoin(final_url, str(im))
                        if not desc and x.get('description'):
                            desc = clean_text(x.get('description'))
                except Exception:
                    pass
        return {'image': image, 'description': desc, 'pageUrl': final_url}
    except Exception:
        return {}


def apple_search(title, country):
    try:
        url = f'https://itunes.apple.com/search?media=podcast&entity=podcast&limit=20&country={country}&term={quote(title)}'
        raw, _, _ = get(url)
        data = json.loads(raw.decode('utf-8', 'ignore'))
        best, bestscore = None, 0
        for r in data.get('results', []):
            s = score(title, r.get('collectionName') or r.get('trackName') or '')
            if s > bestscore:
                bestscore, best = s, r
        if best and bestscore >= .22:
            art = best.get('artworkUrl600') or best.get('artworkUrl100') or best.get('artworkUrl60') or ''
            art = re.sub(r'/\d+x\d+bb', '/1200x1200bb', art)
            return {
                'image': art,
                'feedUrl': best.get('feedUrl', ''),
                'appleUrl': best.get('collectionViewUrl', ''),
                'matched': best.get('collectionName', ''),
                'score': bestscore,
                'country': country,
            }
    except Exception:
        pass
    return {}


def rss_meta(feed):
    if not feed:
        return {}
    try:
        raw, _, final_feed = get(feed, 25)
        root = ET.fromstring(raw)
        ch = root.find('channel')
        if ch is None:
            return {}
        desc, image = '', ''
        for tag in ['{http://www.itunes.com/dtds/podcast-1.0.dtd}summary', 'description']:
            el = ch.find(tag)
            if el is not None and clean_text(el.text):
                desc = clean_text(el.text)
                break
        itimg = ch.find('{http://www.itunes.com/dtds/podcast-1.0.dtd}image')
        if itimg is not None:
            image = itimg.attrib.get('href', '')
        if not image:
            el = ch.find('image/url')
            if el is not None:
                image = (el.text or '').strip()
        if image:
            image = urljoin(final_feed, image)
        return {'image': image, 'description': desc, 'sourceUrl': final_feed}
    except Exception:
        return {}


def image_type(data, content_type=''):
    ct = (content_type or '').split(';')[0].strip().lower()
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if b'<svg' in data[:800].lower():
        return 'svg'
    return {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/svg+xml': 'svg'}.get(ct, '')


def save_cover(pid, candidates):
    # Remove a stale cover generated during this build, if any.
    for old in COVERS.glob(f'{pid}.*'):
        try:
            old.unlink()
        except Exception:
            pass

    seen = set()
    for source_name, url, referer in candidates:
        if not url:
            continue
        url = str(url).replace('http://', 'https://', 1)
        if url in seen:
            continue
        seen.add(url)
        try:
            data, headers, final_url = get(url, 22, referer)
            if len(data) < 1500 or len(data) > 15_000_000:
                continue
            ext = image_type(data, headers.get('Content-Type', ''))
            if not ext:
                continue
            path = COVERS / f'{pid}.{ext}'
            path.write_bytes(data)
            return {
                'localImage': f'covers/{path.name}',
                'originalImage': final_url,
                'imageSource': source_name,
                'bytes': len(data),
            }
        except Exception as e:
            print(f'  cover fail {pid} {source_name}: {str(e)[:100]}')
    return {}


def resolve(p):
    pid, title = p.get('id'), p.get('title', '')
    out = {'title': title, 'sourcePage': p.get('url', '')}

    pm = page_meta(p.get('url', '')) if p.get('url', '').startswith('http') else {}

    # Search several storefronts because catalog availability differs by country.
    apples = []
    for country in ('au', 'us', 'gb', 'ca'):
        a = apple_search(title, country)
        if a:
            apples.append(a)
    ap = max(apples, key=lambda x: x.get('score', 0), default={})
    rm = rss_meta(ap.get('feedUrl', '')) if ap else {}

    desc = rm.get('description') or pm.get('description') or p.get('desc', '')
    source_name = 'Publisher RSS' if rm.get('description') else ('Podcast App' if pm.get('description') else ('Apple Podcasts' if ap else 'Atlas'))
    source_url = rm.get('sourceUrl') or pm.get('pageUrl') or p.get('url', '') or ap.get('appleUrl', '')

    # Candidate order deliberately tries publisher RSS first, then directory metadata,
    # then multiple Apple artwork endpoints. Every successful image is copied locally.
    candidates = []
    if rm.get('image'):
        candidates.append(('Publisher RSS', rm['image'], rm.get('sourceUrl', '')))
    if pm.get('image'):
        candidates.append(('Podcast App', pm['image'], pm.get('pageUrl', p.get('url', ''))))
    for a in sorted(apples, key=lambda x: x.get('score', 0), reverse=True):
        if a.get('image'):
            candidates.append((f"Apple Podcasts {a.get('country', '').upper()}", a['image'], a.get('appleUrl', '')))

    cover = save_cover(pid, candidates)
    image = cover.get('localImage', '')

    out.update({
        'image': image,
        'originalImage': cover.get('originalImage', ''),
        'imageSource': cover.get('imageSource', ''),
        'description': desc,
        'sourceName': source_name,
        'sourceUrl': source_url,
        'appleUrl': ap.get('appleUrl', '') if ap else '',
        'feedUrl': ap.get('feedUrl', '') if ap else '',
        'matched': ap.get('matched', '') if ap else '',
    })
    print(f"{pid} {title[:40]} -> desc={source_name} local-cover={'yes' if image else 'NO'} via={cover.get('imageSource','-')}")
    return pid, out


def main():
    pods = load_podcasts()
    meta = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(resolve, p) for p in pods]
        for f in as_completed(futs):
            try:
                k, v = f.result()
                meta[k] = v
            except Exception as e:
                print('resolver error', e)

    out = 'window.PODCAST_META = ' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ';\n'
    (ROOT / 'source-metadata.js').write_text(out, encoding='utf-8')
    downloaded = len(list(COVERS.glob('pod-*.*')))
    print('wrote source-metadata.js', len(meta), 'shows; downloaded covers:', downloaded)


if __name__ == '__main__':
    main()
