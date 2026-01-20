(() => {
  const listEl = document.getElementById('docsList');
  const contentEl = document.getElementById('docsContent');
  if (!listEl || !contentEl) return;

  const state = {
    files: [],
    current: null,
    cache: new Map(),
  };

  const escapeHtml = (text) =>
    String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

  const escapeAttr = (text) => String(text || '').replace(/"/g, '&quot;');

  const inlineFormat = (text) => {
    let out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    out = out.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_m, alt, url) => {
      return `<img alt="${alt}" src="${escapeAttr(url)}">`;
    });
    out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, url) => {
      return `<a href="${escapeAttr(url)}">${label}</a>`;
    });
    return out;
  };

  function renderMarkdown(md) {
    const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (line.trim().startsWith('<details')) {
        const block = [];
        while (i < lines.length) {
          block.push(lines[i]);
          if (lines[i].trim().startsWith('</details>')) {
            i += 1;
            break;
          }
          i += 1;
        }
        out.push(block.join('\n'));
        continue;
      }
      if (line.trim().startsWith('```')) {
        const lang = line.trim().slice(3).trim();
        const code = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          code.push(lines[i]);
          i += 1;
        }
        out.push(
          `<pre><code class="language-${escapeAttr(lang)}">${escapeHtml(code.join('\n'))}</code></pre>`
        );
        i += 1;
        continue;
      }
      if (line.trim().startsWith('>')) {
        const quote = [];
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quote.push(lines[i].replace(/^>\s?/, ''));
          i += 1;
        }
        out.push(`<blockquote>${inlineFormat(quote.join(' '))}</blockquote>`);
        continue;
      }
      const tableSep = lines[i + 1] || '';
      if (line.includes('|') && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(tableSep)) {
        const header = line
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((cell) => inlineFormat(cell.trim()));
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|')) {
          const row = lines[i]
            .trim()
            .replace(/^\|/, '')
            .replace(/\|$/, '')
            .split('|')
            .map((cell) => inlineFormat(cell.trim()));
          rows.push(row);
          i += 1;
        }
        const headerHtml = header.map((cell) => `<th>${cell}</th>`).join('');
        const bodyHtml = rows
          .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`)
          .join('');
        out.push(`<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`);
        continue;
      }
      if (/^#{1,6}\s+/.test(line)) {
        const level = Math.min(6, line.match(/^#{1,6}/)[0].length);
        const text = line.replace(/^#{1,6}\s+/, '');
        out.push(`<h${level}>${inlineFormat(text)}</h${level}>`);
        i += 1;
        continue;
      }
      if (/^(\d+\.)\s+/.test(line) || /^[-*]\s+/.test(line)) {
        const ordered = /^(\d+\.)\s+/.test(line);
        const items = [];
        while (i < lines.length && (/^(\d+\.)\s+/.test(lines[i]) || /^[-*]\s+/.test(lines[i]))) {
          const itemText = lines[i].replace(/^(\d+\.)\s+/, '').replace(/^[-*]\s+/, '');
          items.push(`<li>${inlineFormat(itemText)}</li>`);
          i += 1;
        }
        out.push(ordered ? `<ol>${items.join('')}</ol>` : `<ul>${items.join('')}</ul>`);
        continue;
      }
      if (!line.trim()) {
        i += 1;
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim()) {
        para.push(lines[i]);
        i += 1;
      }
      out.push(`<p>${inlineFormat(para.join(' '))}</p>`);
    }
    return out.join('\n');
  }

  function resolvePath(basePath, rel) {
    if (!rel) return null;
    const trimmed = rel.replace(/^docs\//, '').replace(/^\//, '');
    const baseDir = basePath ? basePath.split('/').slice(0, -1) : [];
    const parts = trimmed.split('/');
    const stack = [...baseDir];
    for (const part of parts) {
      if (!part || part === '.') continue;
      if (part === '..') {
        stack.pop();
      } else {
        stack.push(part);
      }
    }
    return stack.join('/');
  }

  function rewriteDocLinks(basePath) {
    contentEl.querySelectorAll('img').forEach((img) => {
      const src = img.getAttribute('src') || '';
      if (/^(https?:)?\/\//i.test(src) || src.startsWith('data:')) return;
      const cleaned = src.replace(/^docs\//, '').replace(/^\.\//, '');
      const resolved = resolvePath(basePath, cleaned);
      if (resolved) {
        img.src = `/docs/static/${resolved}`;
      }
    });
    contentEl.querySelectorAll('a').forEach((link) => {
      const href = link.getAttribute('href') || '';
      if (href.startsWith('#')) return;
      if (/^(https?:)?\/\//i.test(href) || href.startsWith('mailto:')) return;
      const [pathPart, hash] = href.split('#');
      const cleaned = pathPart.replace(/^docs\//, '').replace(/^\.\//, '');
      if (!cleaned) return;
      if (cleaned.toLowerCase().endsWith('.md')) {
        const resolved = resolvePath(basePath, cleaned);
        if (resolved) {
          link.setAttribute('data-doc-path', resolved);
        }
      } else {
        const resolved = resolvePath(basePath, cleaned);
        if (resolved) {
          link.href = `/docs/static/${resolved}${hash ? `#${hash}` : ''}`;
        }
      }
    });
  }

  function setActive(path) {
    document.querySelectorAll('.docs-link').forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.path === path);
    });
  }

  async function loadDoc(path) {
    if (!path) return;
    state.current = path;
    setActive(path);
    if (state.cache.has(path)) {
      contentEl.innerHTML = state.cache.get(path);
      rewriteDocLinks(path);
      return;
    }
    contentEl.textContent = 'Loading…';
    const res = await fetch(`/api/docs/get?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      contentEl.textContent = 'Documentation unavailable.';
      return;
    }
    const data = await res.json();
    const html = renderMarkdown(data.markdown || '');
    state.cache.set(path, html);
    contentEl.innerHTML = html || '<div class="muted">Empty document.</div>';
    rewriteDocLinks(path);
  }

  function renderList() {
    listEl.innerHTML = '';
    state.files.forEach((file) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'docs-link';
      btn.dataset.path = file.path;
      btn.textContent = file.title || file.path;
      btn.addEventListener('click', () => loadDoc(file.path));
      listEl.appendChild(btn);
    });
  }

  contentEl.addEventListener('click', (evt) => {
    const target = evt.target.closest('a[data-doc-path]');
    if (!target) return;
    evt.preventDefault();
    const path = target.getAttribute('data-doc-path');
    if (path) loadDoc(path);
  });

  async function init() {
    const res = await fetch('/api/docs/list');
    if (!res.ok) {
      contentEl.textContent = 'Documentation unavailable.';
      return;
    }
    const data = await res.json();
    state.files = Array.isArray(data.files) ? data.files : [];
    renderList();
    const readme = state.files.find((item) => item.path.toLowerCase().endsWith('readme.md'));
    const first = state.files[0];
    loadDoc((readme || first || {}).path);
  }

  init();
})();
