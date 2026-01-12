function setupUtilMenu(toggleId, dropdownId){
  const toggle = document.getElementById(toggleId);
  const dd = document.getElementById(dropdownId);
  if(!toggle || !dd) return;
  const wrapper = toggle.parentElement;
  const open = () => {
    dd.classList.remove('hidden');
    toggle.setAttribute('aria-expanded', 'true');
  };
  const close = () => {
    dd.classList.add('hidden');
    toggle.setAttribute('aria-expanded', 'false');
  };
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    if (dd.classList.contains('hidden')) {
      open();
    } else {
      close();
    }
  });
  document.addEventListener('click', (e)=>{
    if(!dd.contains(e.target) && e.target!==toggle){
      close();
    }
  });
  if (wrapper) {
    wrapper.addEventListener('mouseenter', open);
    wrapper.addEventListener('mouseleave', close);
  }
}

function showToast(msg){
  const el = document.getElementById('toast');
  if(!el) return;
  el.textContent = msg || '';
  el.classList.add('show');
  el.classList.remove('hidden');
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(()=>{ el.classList.remove('show'); }, 1800);
}

function applyFileBrowserFilter(browser){
  if(!browser) return;
  const input = browser.querySelector('.file-browser-search input');
  if(!input) return;
  const term = (input.value || '').trim().toLowerCase();
  browser.querySelectorAll('[data-browser-item]').forEach(item => {
    const title = (item.dataset.title || '').toLowerCase();
    item.style.display = !term || title.includes(term) ? '' : 'none';
  });
}

let badgeMeasureHost = null;
function getBadgeMeasureHost(){
  if(badgeMeasureHost) return badgeMeasureHost;
  const host = document.createElement('div');
  host.style.position = 'absolute';
  host.style.visibility = 'hidden';
  host.style.pointerEvents = 'none';
  host.style.height = '0';
  host.style.overflow = 'hidden';
  document.body.appendChild(host);
  badgeMeasureHost = host;
  return host;
}

function makeBadgeNode(badge){
  const span = document.createElement('span');
  const key = badge.key || 'format';
  span.className = `badge badge-${key}`;
  span.textContent = badge.label || '';
  if (badge.title) span.title = badge.title;
  return span;
}

function measureBadgeWidth(badge){
  const host = getBadgeMeasureHost();
  host.appendChild(badge);
  const width = badge.offsetWidth || 0;
  host.removeChild(badge);
  return width;
}

function computeVisibleBadges(badges, containerWidth){
  if(!badges.length || !containerWidth) return { visible: badges, hidden: [] };
  const ordered = [...badges];
  const gap = 6;
  const reserve = measureBadgeWidth(makeBadgeNode({ key: 'format', label: '+9' })) + gap;
  let used = 0;
  const visible = [];
  const hidden = [];
  ordered.forEach(badge => {
    const w = measureBadgeWidth(makeBadgeNode(badge));
    const next = w + (visible.length ? gap : 0);
    if (used + next + reserve <= containerWidth || visible.length === 0) {
      visible.push(badge);
      used += next;
    } else {
      hidden.push(badge);
    }
  });
  if (!hidden.length) return { visible: ordered, hidden: [] };
  return { visible, hidden };
}

function packBadgesWithRows(badges, containerWidth, maxRows, reserve){
  const gap = 6;
  let row = 1;
  let rowWidth = 0;
  const visible = [];
  let hidden = [];
  const capForRow = (r) => (r === maxRows ? Math.max(0, containerWidth - reserve) : containerWidth);
  for (let i = 0; i < badges.length; i++) {
    const badge = badges[i];
    const w = measureBadgeWidth(makeBadgeNode(badge));
    let cap = capForRow(row);
    let needed = w + (rowWidth ? gap : 0);
    if (rowWidth && rowWidth + needed > cap) {
      row += 1;
      rowWidth = 0;
      cap = capForRow(row);
      needed = w;
    }
    if (row > maxRows || (rowWidth && rowWidth + needed > cap)) {
      hidden = badges.slice(i);
      break;
    }
    visible.push(badge);
    rowWidth += (rowWidth ? gap : 0) + w;
  }
  return { visible, hidden };
}

function computeVisibleBadgesRows(badges, containerWidth, maxRows){
  if(!badges.length || !containerWidth || maxRows <= 1) {
    return computeVisibleBadges(badges, containerWidth);
  }
  const first = packBadgesWithRows(badges, containerWidth, maxRows, 0);
  if (!first.hidden.length) return first;
  const reserveLabel = `+${first.hidden.length}`;
  const reserve = measureBadgeWidth(makeBadgeNode({ key: 'format', label: reserveLabel })) + 6;
  return packBadgesWithRows(badges, containerWidth, maxRows, reserve);
}

function renderBadgeRow(container){
  if(!container) return;
  let badges = [];
  try{
    badges = JSON.parse(container.dataset.badges || '[]');
  }catch(_err){
    badges = [];
  }
  container.innerHTML = '';
  if(!badges.length) return;
  const width = container.clientWidth || container.parentElement?.clientWidth || 0;
  const maxRows = parseInt(container.dataset.maxRows || '0', 10);
  const { visible, hidden } = maxRows > 1
    ? computeVisibleBadgesRows(badges, width, maxRows)
    : computeVisibleBadges(badges, width);
  visible.forEach(b => container.appendChild(makeBadgeNode(b)));
  if (hidden.length) {
    const more = makeBadgeNode({
      key: 'format',
      label: `+${hidden.length}`,
      title: hidden.map(b => b.title || b.label).filter(Boolean).join(', ')
    });
    container.appendChild(more);
  }
}

let badgeLayoutRaf = null;
function layoutBadgeRows(scope){
  const root = scope || document;
  const rows = root.querySelectorAll('.badge-row[data-badges]');
  rows.forEach(renderBadgeRow);
}

// HTMX hooks for quick feedback on deletes
document.addEventListener('htmx:afterSwap', function(evt){
  const elt = evt.target;
  if(!elt) return;
  const isDeleteForm = elt.closest && elt.closest('form.delete-selected-form');
  if(isDeleteForm){
    showToast('Updated');
  }
  layoutBadgeRows(elt);
  const browser = elt.closest && elt.closest('.file-browser');
  if (browser) applyFileBrowserFilter(browser);
  if (elt.matches && elt.matches('[data-autoselect="first"]') && !elt.dataset.autoselected) {
    const first = elt.querySelector('[data-browser-item]');
    if (first) {
      elt.dataset.autoselected = '1';
      first.click();
    }
  }
});

document.addEventListener('htmx:onError', function(evt){
  showToast('Action failed');
});

document.addEventListener('htmx:sendError', function(evt){
  showToast('Network error');
});

document.addEventListener('input', function(evt){
  const input = evt.target;
  if(!input || !input.closest) return;
  const browser = input.closest('.file-browser');
  if(browser && input.matches('.file-browser-search input')){
    applyFileBrowserFilter(browser);
  }
});

document.addEventListener('click', function(evt){
  const toggle = evt.target.closest && evt.target.closest('.file-browser-toggle');
  if(!toggle) return;
  const section = toggle.closest('.file-browser-section');
  if(section) {
    const collapsed = section.classList.toggle('collapsed');
    if (!collapsed) layoutBadgeRows(section);
  }
});

window.addEventListener('resize', function(){
  if (badgeLayoutRaf) cancelAnimationFrame(badgeLayoutRaf);
  badgeLayoutRaf = requestAnimationFrame(() => {
    badgeLayoutRaf = null;
    layoutBadgeRows(document);
  });
});

document.addEventListener('DOMContentLoaded', function(){
  layoutBadgeRows(document);
  document.querySelectorAll('.file-browser').forEach(applyFileBrowserFilter);
});
