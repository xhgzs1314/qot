/* ==========================================================================
   QOT 文档中心 · 统一外壳与增强脚本 (qdoc)
   规范来源：plugin/demos.html
   - 构建左侧目录外壳（标题自动生成锚点、搜索、滚动高亮、移动抽屉）
   - 把每个 <pre><code> 升级为代码卡片（行号 / 高亮 / 复制 / 折叠）
   - 页面正文保持原样，只做增强，不改内容
   ========================================================================== */
(function () {
    'use strict';

    if (window.__qdocMounted) return;
    window.__qdocMounted = true;

    var doc = document;
    var body = doc.body;
    if (!body) return;

    var EMBEDDED = window.self !== window.top;

    function $(sel, root) { return (root || doc).querySelector(sel); }
    function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
    function el(tag, cls, html) {
        var n = doc.createElement(tag);
        if (cls) n.className = cls;
        if (html != null) n.innerHTML = html;
        return n;
    }

    /* ======================================================================
       1. 语法高亮
       ====================================================================== */
    var KW = new Set(('var func return if else eif for in loop stop continue class macro require this ' +
        'true false null while break import export async await').split(' '));
    var TY = new Set('any_t number_t string_t bool_t array_t dict_t void_t'.split(' '));
    var TOKEN_RE = /\/\/[^\n]*|\/\*[\s\S]*?\*\/|L?"(?:[^"\\]|\\[\s\S])*"|\b\d+(?:\.\d+)?\b|[A-Za-z_]\w*/g;

    function esc(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function paint(src) {
        var out = '', last = 0, m;
        TOKEN_RE.lastIndex = 0;
        while ((m = TOKEN_RE.exec(src)) !== null) {
            var t = m[0], ch = t.charAt(0), cls = '';
            if (m.index > last) out += esc(src.slice(last, m.index));
            if (ch === '/') {
                cls = 'tok-com';
            } else if (ch === '"' || (ch === 'L' && t.charAt(1) === '"')) {
                cls = 'tok-str';
            } else if (ch >= '0' && ch <= '9') {
                cls = 'tok-num';
            } else if (KW.has(t)) {
                cls = 'tok-kw';
            } else if (TY.has(t)) {
                cls = 'tok-type';
            } else if (src.charAt(m.index + t.length) === '!') {
                cls = 'tok-macro';
            } else {
                var j = m.index + t.length;
                while (src.charAt(j) === ' ') j++;
                if (src.charAt(j) === '(') cls = 'tok-fn';
            }
            out += cls ? '<span class="' + cls + '">' + esc(t) + '</span>' : esc(t);
            last = m.index + t.length;
        }
        out += esc(src.slice(last));
        return out;
    }

    var ICON_COPY = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" ' +
        'height="11" rx="2"></rect><path d="M5 15V5a2 2 0 0 1 2-2h8"></path></svg>';
    var ICON_CHECK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12">' +
        '</polyline></svg>';

    var COLLAPSE_OVER = 30;

    function copyText(text, btn) {
        function done() {
            btn.classList.add('done');
            btn.innerHTML = ICON_CHECK + '<span>已复制</span>';
            clearTimeout(btn._timer);
            btn._timer = setTimeout(function () {
                btn.classList.remove('done');
                btn.innerHTML = ICON_COPY + '<span>复制</span>';
            }, 1600);
        }
        function fallback() {
            var ta = el('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
            body.appendChild(ta);
            ta.select();
            try { doc.execCommand('copy'); done(); } catch (e) { /* 忽略 */ }
            body.removeChild(ta);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, fallback);
        } else {
            fallback();
        }
    }

    function hasMarkup(code) {
        for (var i = 0; i < code.children.length; i++) {
            if (code.children[i].nodeType === 1) return true;
        }
        return false;
    }

    function buildCard(code) {
        var pre = code.parentNode;
        if (!pre || pre.tagName !== 'PRE' || pre.closest('.code-card')) return;

        var raw = code.textContent.replace(/^[\r\n]+/, '').replace(/\s+$/, '');
        if (!raw) return;

        var lines = raw.split('\n');
        var marked = hasMarkup(code);
        var card = el('figure', 'code-card');
        var gutter = '';
        for (var i = 1; i <= lines.length; i++) gutter += '<span>' + i + '</span>';

        card.innerHTML =
            '<div class="code-head">' +
            '<span class="lang">' + (code.getAttribute('data-lang') || 'QOT') + '</span>' +
            '<span class="code-meta">' + lines.length + ' 行</span>' +
            '<button class="btn-copy" type="button">' + ICON_COPY + '<span>复制</span></button>' +
            '</div>' +
            '<div class="code-scroll">' +
            '<div class="gutter" aria-hidden="true">' + gutter + '</div>' +
            '<pre><code class="tok">' + (marked ? code.innerHTML : paint(raw)) + '</code></pre>' +
            '</div>' +
            '<div class="code-fade" aria-hidden="true"></div>';

        if (lines.length > COLLAPSE_OVER) {
            card.classList.add('folding');
            var btn = el('button', 'code-expand');
            btn.type = 'button';
            btn.innerHTML = '<span>展开全部 ' + lines.length + ' 行</span>' +
                '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
                'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
                '<polyline points="6 9 12 15 18 9"></polyline></svg>';
            btn.addEventListener('click', function () {
                var open = card.classList.toggle('expanded');
                btn.firstChild.textContent = open ? '收起' : ('展开全部 ' + lines.length + ' 行');
                if (!open) card.querySelector('.code-scroll').scrollTop = 0;
            });
            card.appendChild(btn);
        }

        card.querySelector('.btn-copy').addEventListener('click', function () {
            copyText(raw, this);
        });

        pre.parentNode.replaceChild(card, pre);
    }

    function enhanceCode(root) {
        /* 裸 <pre>（无 code、无高亮 span）且行数够多时，升级为统一的代码卡片 */
        $$('pre', root).forEach(function (pre) {
            if (pre.querySelector('code') || pre.querySelector('*')) return;
            var text = pre.textContent;
            if (text.split('\n').length < 3) return;
            var code = doc.createElement('code');
            code.textContent = text;
            pre.removeAttribute('style');
            pre.textContent = '';
            pre.appendChild(code);
        });
        $$('pre > code', root).forEach(buildCard);
    }

    /* ======================================================================
       2. 外壳：侧栏 + 目录
       ====================================================================== */
    var contentSelectors = '.doc-head, .hero, .page-head, .doc-header';

    function slugify(text, i) {
        var s = text.trim().toLowerCase()
            .replace(/[\s\u3000]+/g, '-')
            .replace(/[^\w\u4e00-\u9fa5-]/g, '')
            .replace(/-+/g, '-')
            .replace(/^-|-$/g, '');
        return s ? ('sec-' + s) : ('sec-' + i);
    }

    function makeHero(main) {
        var legacy = main.querySelector(contentSelectors);
        if (legacy) {
            if (!legacy.querySelector('.eyebrow, .q-eyebrow')) {
                legacy.insertBefore(el('span', 'q-eyebrow', 'QOT Docs'), legacy.firstChild);
            }
            return;
        }

        var h1 = null;
        var kids = main.children;
        for (var i = 0; i < kids.length && i < 4; i++) {
            if (kids[i].tagName === 'H1') { h1 = kids[i]; break; }
        }
        /* 标题被包在卡片/容器里：不另造头图，避免标题重复 */
        if (!h1) {
            var nested = main.querySelector('h1');
            if (nested) {
                if (!nested.closest('.card') && !nested.parentNode.querySelector('.q-eyebrow')) {
                    nested.parentNode.insertBefore(el('span', 'q-eyebrow', 'QOT Docs'), nested);
                }
                return;
            }
        }

        var hero = el('div', 'q-hero');
        var eyebrow = el('span', 'q-eyebrow', 'QOT Docs');
        hero.appendChild(eyebrow);

        if (h1) {
            var next = h1.nextElementSibling;
            hero.appendChild(h1);
            if (next && next.tagName === 'P') hero.appendChild(next);
            main.insertBefore(hero, main.firstChild);
        } else {
            var title = (doc.title || 'QOT 文档').split(/[·|-]/)[0].trim();
            hero.appendChild(el('h1', null, esc(title)));
            main.insertBefore(hero, main.firstChild);
        }
    }

    function collectHeadings(main) {
        var h2s = $$('h2', main).filter(function (h) {
            return !h.closest('.code-card') && h.textContent.trim().length > 0;
        });
        var h3s = $$('h3', main).filter(function (h) {
            return !h.closest('.code-card') && h.textContent.trim().length > 0;
        });
        var withSub = h3s.length > 0 && h3s.length <= 30 && h2s.length > 0;
        var list = [];
        /* 正文 h2 太少（多为脚本渲染）时，用 h3 充当一级目录 */
        if (h2s.length < 4 && h3s.length >= 2) {
            h2s = h3s.slice(0, 30);
            withSub = false;
        } else if (!h2s.length) {
            h2s = h3s.slice(0, 30);
            withSub = false;
        }
        h2s.forEach(function (h, i) {
            if (!h.id) h.id = slugify(h.textContent, i + 1);
            list.push({ el: h, level: 2, title: h.textContent.trim() });
            if (!withSub) return;
            var n = h.nextElementSibling;
            while (n && n.tagName !== 'H2') {
                if (n.tagName === 'H3' && !n.closest('.code-card')) {
                    if (!n.id) n.id = slugify(n.textContent, list.length + 1);
                    list.push({ el: n, level: 3, title: n.textContent.trim() });
                }
                n = n.nextElementSibling;
            }
        });
        return list;
    }

    function buildShell(main, headings) {
        var brandHref = /(^|\/)index\.html$/.test(location.pathname) ? '#top' : 'index.html';
        var sidebar = el('aside', 'q-sidebar');
        sidebar.id = 'qSidebar';
        sidebar.innerHTML =
            '<a class="q-brand" href="' + brandHref + '">' +
            '<span class="q-brand-mark">Q</span>' +
            '<span><span class="q-brand-name">QOT 文档</span>' +
            '<span class="q-brand-sub">Documentation</span></span></a>' +
            '<div class="q-search">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.6-3.6"></path></svg>' +
            '<input id="qSearch" type="search" placeholder="搜索目录 / 标题" autocomplete="off" spellcheck="false">' +
            '<kbd>/</kbd></div>' +
            '<p class="q-nav-label">本页目录</p>' +
            '<ul class="q-nav" id="qNav"></ul>' +
            '<p class="q-empty" id="qEmpty">没有匹配的条目</p>' +
            '<div class="q-side-foot">' +
            '<a href="index.html"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M19 12H5M11 18l-6-6 6-6"></path></svg>文档中心</a>' +
            '<a href="demos.html"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M4 6h16M4 12h16M4 18h10"></path></svg>学习例子</a>' +
            '</div>';

        var wrap = el('div', 'q-layout' + (EMBEDDED ? ' q-embedded' : ''));
        wrap.appendChild(sidebar);
        wrap.appendChild(main);
        body.appendChild(wrap);

        var nav = $('#qNav', sidebar);
        var links = [];
        var n2 = 0;

        headings.forEach(function (h) {
            var li = el('li', h.level === 3 ? 'q-sub' : '');
            var a = el('a');
            a.href = '#' + h.el.id;
            var label = h.title.replace(/^\d+(?:\.\d+)*[.、．]?\s+/, '');
            a.innerHTML = (h.level === 2 ? '<span class="n">' + pad(++n2) + '</span>' : '') +
                '<span class="t">' + esc(label || h.title) + '</span>';
            li.appendChild(a);
            nav.appendChild(li);
            links.push({ a: a, li: li, el: h.el });
            a.addEventListener('click', function (e) {
                if (reveal(h.el)) {
                    e.preventDefault();
                    h.el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    history.replaceState(null, '', '#' + h.el.id);
                }
                closeDrawer();
            });
        });

        return { wrap: wrap, sidebar: sidebar, nav: nav, links: links };
    }

    function pad(n) { return (n < 10 ? '0' : '') + n; }

    /* 目标被折叠时先展开 */
    function reveal(node) {
        var p = node;
        while (p && p !== body) {
            if (p.nodeType === 1 && p.style && p.style.display === 'none') {
                var prev = p.previousElementSibling;
                if (prev && /header/i.test(prev.className)) {
                    prev.click();
                } else {
                    p.style.display = '';
                }
                return true;
            }
            p = p.parentNode;
        }
        return false;
    }

    /* ======================================================================
       3. 交互：搜索 / 滚动高亮 / 进度 / 抽屉
       ====================================================================== */
    function wire(shell) {
        var links = shell.links;
        var nav = shell.nav;
        var progress = el('div', 'q-progress');
        progress.id = 'qProgress';
        var toTop = el('button', 'q-to-top');
        toTop.type = 'button';
        toTop.setAttribute('aria-label', '回到顶部');
        toTop.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7">' +
            '</path></svg>';
        body.appendChild(progress);
        body.appendChild(toTop);

        if (!EMBEDDED) {
            var menuBtn = el('button', 'q-menu-btn');
            menuBtn.type = 'button';
            menuBtn.setAttribute('aria-label', '打开目录');
            menuBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
                'stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"></path></svg>';
            var scrim = el('div', 'q-scrim');
            body.appendChild(menuBtn);
            body.appendChild(scrim);

            menuBtn.addEventListener('click', function () {
                var open = shell.sidebar.classList.toggle('open');
                scrim.classList.toggle('show', open);
            });
            scrim.addEventListener('click', closeDrawer);
            doc.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') closeDrawer();
            });
        }

        function closeDrawer() {
            shell.sidebar.classList.remove('open');
            var s = $('.q-scrim');
            if (s) s.classList.remove('show');
        }

        toTop.addEventListener('click', function () {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });

        var ticking = false;
        function onScroll() {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(function () {
                ticking = false;
                var y = window.pageYOffset || doc.documentElement.scrollTop;
                var max = doc.documentElement.scrollHeight - window.innerHeight;
                progress.style.width = (max > 0 ? Math.min(100, (y / max) * 100) : 0) + '%';
                toTop.classList.toggle('show', y > 600);

                var active = -1;
                for (var i = 0; i < links.length; i++) {
                    if (links[i].el.getBoundingClientRect().top <= 140) active = i;
                    else break;
                }
                if (active < 0) active = 0;
                if (y + window.innerHeight >= doc.documentElement.scrollHeight - 4) {
                    active = links.length - 1;
                }
                links.forEach(function (item, i) {
                    item.a.classList.toggle('active', i === active);
                });
                var cur = links[active];
                if (cur) {
                    var r = cur.a.getBoundingClientRect();
                    var box = nav.getBoundingClientRect();
                    if (r.top < box.top || r.bottom > box.bottom) {
                        cur.a.scrollIntoView({ block: 'nearest' });
                    }
                }
            });
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        window.addEventListener('resize', onScroll);
        onScroll();

        var search = $('#qSearch');
        var empty = $('#qEmpty');
        if (search) {
            search.addEventListener('input', function () {
                var q = search.value.trim().toLowerCase();
                var shown = 0;
                links.forEach(function (item) {
                    var hit = !q || item.el.textContent.toLowerCase().indexOf(q) !== -1;
                    item.li.classList.toggle('q-hidden', !hit);
                    if (hit) shown++;
                });
                empty.classList.toggle('show', shown === 0);
            });
            search.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') {
                    search.value = '';
                    links.forEach(function (item) { item.li.classList.remove('q-hidden'); });
                    empty.classList.remove('show');
                    search.blur();
                }
            });
            doc.addEventListener('keydown', function (e) {
                var tag = (doc.activeElement || {}).tagName;
                if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
                    e.preventDefault();
                    search.focus();
                    search.select();
                }
            });
        }
    }

    /* ======================================================================
       4. 启动
       ====================================================================== */
    function mount() {
        var existing = $('.q-layout');
        var main;

        if (existing) {
            main = $('.q-main', existing);
        } else {
            main = el('main', 'q-main');
            main.id = 'qMain';

            var moved = [];
            Array.prototype.slice.call(body.children).forEach(function (n) {
                var t = n.tagName;
                if (t === 'SCRIPT' || t === 'STYLE' || t === 'LINK' || t === 'NOSCRIPT' || t === 'TEMPLATE') return;
                moved.push(n);
            });
            moved.forEach(function (n) { main.appendChild(n); });
        }

        if (!main) return;

        enhanceCode(main);
        $$('table', main).forEach(function (tb) {
            if (tb.parentNode && tb.parentNode.classList.contains('table-wrap')) return;
            var wrap = el('div', 'table-wrap');
            tb.parentNode.insertBefore(wrap, tb);
            wrap.appendChild(tb);
        });

        var skip = el('a', 'q-skip', '跳到正文');
        skip.href = '#' + (main.id || 'qMain');
        body.insertBefore(skip, body.firstChild);

        if (existing) return;

        makeHero(main);
        if (main.querySelector('.q-hero, .doc-head, .hero, .page-head, .doc-header')) {
            main.classList.add('q-has-hero');
        }
        var headings = collectHeadings(main);
        var shell = buildShell(main, headings);
        wire(shell);
        doc.documentElement.classList.add('q-ready');
    }

    if (doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
