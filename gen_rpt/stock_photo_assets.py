"""Reviewed GateX stock catalogue and local assets; no AI or supplier fallback.

Topic selection mirrors GateX lib/stock-photos.ts. A secondary description can
rank eligible photos, but cannot make an unrelated photo eligible.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from PIL import Image, ImageOps

CATALOG_URL = 'https://gatex.fund/images/stock/catalog.json'
STOCK_MODEL = 'licensed-stock-v1'
TOPICS = (
    ('semiconductor','chip','GPU','artificial intelligence','AI','robot','data center','data centre','computing','cloud infrastructure','AI infrastructure','数据中心','算力','云计算','半导体','芯片','人工智能','机器人'),
    ('healthcare','medical','hospital','pharmaceutical','医疗','医院','医药'),
    ('solar','photovoltaic','太阳能','光伏'),
    ('oil','gas','petroleum','crude','石油','原油','天然气'),
    ('energy','renewable energy','energy transition','wind power','能源','可再生能源','能源转型','风电'),
    ('logistics','shipping','container','supply chain','物流','航运','集装箱','供应链'),
    ('airport','aviation','airline','机场','航空'),
    ('finance','investment','bank','capital markets','stock exchange','equity','金融','投资','银行','资本市场','证券交易所','股权'),
)
GEOGRAPHIES = ('Singapore','新加坡','Hong Kong','香港','Frankfurt','法兰克福','United Arab Emirates','UAE','阿联酋','Saudi Arabia','Saudi','沙特','China','中国','United States','USA','美国','United Kingdom','UK','英国','Germany','德国','Japan','日本','India','印度','Qatar','卡塔尔','Bahrain','巴林','Kuwait','科威特','Oman','阿曼','Australia','澳大利亚','Canada','加拿大','Malaysia','马来西亚','Indonesia','印度尼西亚','Thailand','泰国','Vietnam','越南','Gulf','海湾','Middle East','中东','Dubai','迪拜','Riyadh','利雅得','GCC','海合会','MENA','中东北非','Abu Dhabi','阿布扎比','Shanghai','上海','Beijing','北京','Shenzhen','深圳')


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).lower().split())


def matches(text, keyword):
    key = normalized(keyword)
    if not key: return False
    if re.search(r'[\u3400-\u9fff]', key): return key in text
    return bool(re.search(r'(^|[^a-z0-9_])' + re.escape(key) + r'(?:s|es)?(?=$|[^a-z0-9_])', text))


def _specificity(keyword):
    key = normalized(keyword)
    if re.search(r'[\u3400-\u9fff]', key): return 2 if len(key) >= 4 else 1
    words = len(key.split(' '))
    return words + 1 if words > 1 else 1


def _signature(value):
    result = 2166136261
    for char in value:
        # JS for-of uses each Unicode scalar, charCodeAt(0) its first UTF-16 unit.
        unit = int.from_bytes(char.encode('utf-16-le')[:2], 'little')
        result = ((result ^ unit) * 16777619) & 0xffffffff
    return result


def select_stock_photo(title, catalog, *, industry='', summary='', seed=None, exclude_ids=()):
    original_title = str(title or '')
    title, industry, summary = map(normalized, (title, industry, summary))
    primary = title + ' ' + industry
    topics = [words for words in TOPICS if any(matches(primary, word) for word in words)]
    energy = [words for words in topics if words in (TOPICS[2], TOPICS[3])]
    required = energy or topics
    explicit_location = any(matches(primary, location) for location in GEOGRAPHIES)
    ranked = []
    for photo in catalog:
        if photo['id'] in exclude_ids: continue
        keywords = photo.get('keywords') or []
        if not any(matches(title, word) or matches(industry, word) for word in keywords): continue
        if photo.get('locations') and explicit_location and not any(matches(primary, place) for place in photo['locations']): continue
        if required and not any(any(matches(normalized(keyword), word) for word in words for keyword in keywords) for words in required): continue
        score = sum(_specificity(word) * ((5 if matches(title, word) else 0) + (7 if matches(industry, word) else 0) + (1 if matches(summary, word) else 0)) for word in keywords)
        if score: ranked.append((-score, _signature(f'{seed or original_title}:{photo["id"]}'), photo['id'], photo))
    ranked.sort(key=lambda item: item[:3])
    return ranked[0][3] if ranked else None


def stock_photo_source(photo):
    host = urlsplit(photo['sourcePage']).hostname or ''
    origin = ('wikimedia-commons' if host.endswith('wikimedia.org') else 'pexels' if host.endswith('pexels.com') else 'unsplash' if host.endswith('unsplash.com') else host or 'licensed-library')
    source = dict(id=photo['id'], provider='stock', origin=origin, title=photo['title'], author=photo['author'], license=photo['license'], licenseUrl=photo['licenseUrl'], sourceUrl=photo['sourcePage'], imageUrl=photo['imageUrl'], width=photo['width'], height=photo['height'], credit=f'{photo["title"]} — {photo["author"]} ({photo["license"]})')
    if photo.get('objectPosition'): source['objectPosition'] = photo['objectPosition']
    return source


def _read(url, limit):
    if urlsplit(url).scheme != 'https': raise ValueError('Stock assets require HTTPS')
    with urlopen(Request(url, headers={'User-Agent':'GateX-Stock-Publisher/1.0','Accept':'application/json,image/*'}), timeout=35) as response:
        if urlsplit(response.geturl()).netloc != urlsplit(url).netloc: raise ValueError('Stock asset changed origin')
        blob = response.read(limit + 1)
    if len(blob) > limit: raise ValueError('Stock response exceeds limit')
    return blob


def catalog_url():
    return os.getenv('GATEX_STOCK_CATALOG_URL', CATALOG_URL).strip() or CATALOG_URL


def load_catalog(url=None):
    value = json.loads(_read(url or catalog_url(), 512 * 1024))
    if value.get('version') != 1 or not isinstance(value.get('photos'), list): raise ValueError('Unsupported stock catalogue')
    for photo in value['photos']:
        if not re.fullmatch(r'/images/stock/[a-z0-9-]+\.(?:jpg|jpeg|png)', photo.get('src','')): raise ValueError('Invalid stock local asset')
        if not all(photo.get(key) for key in ('id','title','author','license','licenseUrl','sourcePage','imageUrl','width','height')): raise ValueError('Stock provenance missing')
    return value['photos']


def photo_bytes(photo, url=None):
    src = photo.get('src','')
    if not re.fullmatch(r'/images/stock/[a-z0-9-]+\.(?:jpg|jpeg|png)', src): raise ValueError('Invalid stock local asset')
    return _read(urljoin(url or catalog_url(), src), 16 * 1024 * 1024)


def save_stock_photo(photo, target, *, size=(1536,1024), portrait_cover=False, blob=None):
    """Crop reviewed raster locally. Preserve canonical origin separately from output SHA."""
    data = photo_bytes(photo) if blob is None else blob
    with Image.open(io.BytesIO(data)) as original:
        original.load()
        image = ImageOps.exif_transpose(original).convert('RGB')
        if image.width < 600 or image.height < 450: raise ValueError('Stock resolution is too small')
        position = re.fullmatch(r'(\d+(?:\.\d+)?)% (\d+(?:\.\d+)?)%', photo.get('objectPosition','50% 50%'))
        centering = tuple(min(1.,max(0.,float(value)/100)) for value in position.groups()) if position else (.5,.5)
        if portrait_cover:
            width,height=size
            canvas=Image.new('RGB', size, '#081D38')
            lower=int(height*.45)
            canvas.paste(ImageOps.fit(image,(width,lower),Image.Resampling.LANCZOS,centering=centering),(0,height-lower))
            image=canvas
        else: image=ImageOps.fit(image,size,Image.Resampling.LANCZOS,centering=centering)
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.tmp')
    image.save(temporary,format='PNG' if target.suffix.lower()=='.png' else 'JPEG',quality=93,optimize=True)
    temporary.replace(target)
    return {'source':stock_photo_source(photo),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size,'width':image.width,'height':image.height,'sourceImageSha256':hashlib.sha256(data).hexdigest(),'cropApplied':True}


def prepare_stock_photo(title, target, *, industry='', summary='', seed=None, exclude_ids=(), size=(1536,1024), portrait_cover=False, catalog=None):
    photo=select_stock_photo(title, load_catalog() if catalog is None else catalog, industry=industry,summary=summary,seed=seed,exclude_ids=exclude_ids)
    if not photo:return None
    return save_stock_photo(photo,target,size=size,portrait_cover=portrait_cover)
