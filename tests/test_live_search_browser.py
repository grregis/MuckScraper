"""Shared header search in both themes and phone/desktop layouts."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('width,theme', [(1440,'light'),(1440,'dark'),(393,'light'),(393,'dark')])
def test_shared_header_search_is_keyboard_safe_and_dismisses_late_results(width, theme):
    playwright = pytest.importorskip('playwright.sync_api')
    env = Environment(loader=FileSystemLoader(ROOT/'aggregator/templates'), autoescape=select_autoescape())
    env.filters['get_big_picture'] = lambda value:value
    env.globals.update(url_for=lambda endpoint, **kw: '/static/'+kw['filename'] if endpoint=='static' else '/'+endpoint.replace('.', '/'),
        can=lambda role: role=='reader', current_user=SimpleNamespace(is_authenticated=True,username='Reader',role_label='Reader'),
        request=SimpleNamespace(query_string='',path='/headlines',full_path='/headlines'),config={'DISPLAY_TIMEZONE':'UTC'},
        current_back=lambda:'/headlines', menu_back=lambda:'/headlines',csrf_token=lambda:'fixture')
    html = env.get_template('headlines_feed.html').render(stories=[],page=1,total_pages=1,edition=None,edition_label='Sample edition',is_past_edition=False)
    data={'q':'names','stories':[{'title':'<script>alert(1)</script> A sample international headline', 'url':'/story/7','articles':3,'sources':2,'left':1,'center':1,'right':1,'date':'2026-10-28'}], 'articles':[], 'story_total':1, 'all_url':'/admin/search?q=names'}
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':width,'height':900})
        errors=[]
        page.on('pageerror', lambda e:errors.append(str(e)))
        pending=[]
        hold=[False]
        def route(r):
            url=r.request.url
            if '/admin/search_suggest?' in url:
                if hold[0]: pending.append(r)
                else:r.fulfill(body=json.dumps(data),content_type='application/json')
            elif '/static/' in url:
                path=ROOT/'aggregator/static'/url.split('/static/',1)[1]
                r.fulfill(body=path.read_bytes(),content_type='text/css' if path.suffix=='.css' else 'text/javascript')
            elif url.endswith('/story/7'):r.fulfill(body='<p>Story opened</p>',content_type='text/html')
            elif url=='https://reader.example/':r.fulfill(body=html,content_type='text/html')
            else:r.abort()
        page.route('**/*',route)
        page.goto('https://reader.example/')
        page.evaluate('theme=>document.documentElement.dataset.theme=theme',theme)
        if page.locator('#ft-search-toggle').is_visible():page.locator('#ft-search-toggle').click()
        page.locator('#feed-search').fill('names')
        page.locator('.ft-hit').wait_for()
        assert page.locator('.ft-hit-title').inner_text().startswith('<script>')
        assert page.locator('.ft-hit script').count()==0
        page.keyboard.press('ArrowDown')
        assert page.locator('#feed-search').get_attribute('aria-activedescendant')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        panel=page.locator('#feed-suggest').bounding_box()
        assert panel['x']>=0 and panel['x']+panel['width']<=width
        page.screenshot(path=f'/tmp/muck-visual-series/search-{width}-{theme}.png')
        hold[0]=True
        page.locator('#feed-search').fill('another name')
        page.wait_for_timeout(250)
        assert pending
        page.keyboard.press('Escape')
        pending.pop().fulfill(body=json.dumps({**data,'q':'another name'}),content_type='application/json')
        page.wait_for_timeout(50)
        assert page.locator('#feed-search').get_attribute('aria-expanded')=='false'
        assert not errors
        browser.close()
