/* ===== Дополнительные возможности: фильтры, подписки на поиск, история поиска, недавние,
   подгрузка ленты, просмотр фото, жалобы, подсказки при первом запуске, ссылки из Telegram ===== */

const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
};

/* ---------- Сжатие фото перед загрузкой ---------- */
async function compressImage(file, maxSide = 1600, quality = 0.85) {
  try {
    if (!file || !/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 300 * 1024) return file;
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * k);
    canvas.height = Math.round(bmp.height * k);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', quality));
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
  } catch (e) {
    return file;
  }
}

/* ---------- Фильтры ---------- */
const emptyFilters = () => ({ price_min: '', price_max: '', city: '', delivery: '', condition: '', brand: [] });
let filters = emptyFilters();
const CONDITION_LABELS = { new_tag: 'Новое с биркой', new: 'Новое без бирки', excellent: 'Отличное', good: 'Хорошее', fair: 'Нормально', poor: 'Плохое' };
const DELIVERY_LABELS = { pickup: 'Самовывоз', post: 'Почта' };
let citiesCache = null;

function filtersActive() { return Object.values(filters).some((v) => (Array.isArray(v) ? v.length > 0 : v !== '')); }

function addFilterParams(params) {
  Object.entries(filters).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((x) => params.append(k, x));
    else if (v !== '') params.append(k, String(v).replace(',', '.'));
  });
}

function updateFilterUi() {
  document.getElementById('filterDot')?.classList.toggle('hidden', !filtersActive());
  const box = document.getElementById('activeFilters');
  if (!box) return;
  const chips = [];
  if (filters.price_min !== '' || filters.price_max !== '') {
    const t = `Цена ${filters.price_min !== '' ? 'от ' + filters.price_min : ''} ${filters.price_max !== '' ? 'до ' + filters.price_max : ''}`.replace(/\s+/g, ' ').trim();
    chips.push({ label: t, clear: ['price_min', 'price_max'] });
  }
  if (filters.city) chips.push({ label: filters.city, clear: ['city'] });
  if (filters.delivery) chips.push({ label: DELIVERY_LABELS[filters.delivery], clear: ['delivery'] });
  if (filters.condition) chips.push({ label: CONDITION_LABELS[filters.condition], clear: ['condition'] });
  filters.brand.forEach((b) => chips.push({ label: b, drop: b }));
  box.classList.toggle('hidden', !chips.length);
  box.innerHTML = '';
  chips.forEach((c) => {
    const b = document.createElement('button');
    b.className = 'chip active';
    b.innerHTML = `${esc(c.label)}<span class="x">✕</span>`;
    b.onclick = () => {
      if (c.drop) filters.brand = filters.brand.filter((x) => x !== c.drop);
      else c.clear.forEach((k) => { filters[k] = ''; });
      updateFilterUi(); loadMarket(true);
    };
    box.appendChild(b);
  });
}

async function openFilterModal() {
  document.getElementById('fPriceMin').value = filters.price_min;
  document.getElementById('fPriceMax').value = filters.price_max;
  document.querySelectorAll('#fDelivery .chip').forEach((c) => c.classList.toggle('active', c.dataset.d === filters.delivery));
  document.querySelectorAll('#fCondition .chip').forEach((c) => c.classList.toggle('active', c.dataset.c === filters.condition));
  initBrandPanel();
  const sel = document.getElementById('fCity');
  document.getElementById('filterModal').classList.remove('hidden');
  if (!citiesCache) {
    const res = await apiGet('/api/market/cities');
    citiesCache = isError(res) ? [] : res;
  }
  sel.innerHTML = '<option value="">Все города</option>' + citiesCache.map((c) => `<option value="${esc(c.city)}">${esc(c.city)} (${c.count})</option>`).join('');
  sel.value = filters.city;
}

function readFilterForm() {
  const num = (id) => {
    const v = document.getElementById(id).value.trim().replace(',', '.');
    return v !== '' && Number.isFinite(Number(v)) && Number(v) >= 0 ? v : '';
  };
  return {
    price_min: num('fPriceMin'), price_max: num('fPriceMax'),
    city: document.getElementById('fCity').value,
    delivery: document.querySelector('#fDelivery .chip.active')?.dataset.d || '',
    condition: document.querySelector('#fCondition .chip.active')?.dataset.c || '',
    brand: [...fBrandSet],
  };
}

document.getElementById('filterBtn')?.addEventListener('click', openFilterModal);
document.getElementById('fDelivery')?.addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  document.querySelectorAll('#fDelivery .chip').forEach((c) => c.classList.toggle('active', c === chip));
});
document.getElementById('fCondition')?.addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  document.querySelectorAll('#fCondition .chip').forEach((c) => c.classList.toggle('active', c === chip));
});
document.getElementById('fApply')?.addEventListener('click', () => {
  filters = readFilterForm();
  document.getElementById('filterModal').classList.add('hidden');
  updateFilterUi();
  loadMarket(true);
});
document.getElementById('fReset')?.addEventListener('click', () => {
  filters = emptyFilters();
  document.getElementById('filterModal').classList.add('hidden');
  updateFilterUi();
  loadMarket(true);
});
document.getElementById('filterModal')?.addEventListener('click', (e) => { if (e.target.id === 'filterModal') e.currentTarget.classList.add('hidden'); });

/* ---------- Подписки на поиск ---------- */
document.getElementById('fSave')?.addEventListener('click', async () => {
  const f = readFilterForm();
  const q = document.getElementById('searchInput').value.trim();
  const cat = document.getElementById('categoryFilter').value;
  if (!q && !cat) { notify('Введите запрос в поиске или выберите категорию — по ним бот будет искать новинки.'); return; }
  const res = await apiPost('/api/saved-searches', {
    q: q || null, category_id: cat ? Number(cat) : null,
    price_max: f.price_max !== '' ? Number(f.price_max) : null, city: f.city || null,
  });
  if (isError(res)) { notify(res.detail); return; }
  notify('Готово! Бот напишет вам, когда появится подходящее объявление. Подписки можно отключить в «Настройках».');
  renderSavedSearches();
});

async function renderSavedSearches() {
  const box = document.getElementById('savedSearchList');
  if (!box) return;
  const rows = await apiGet('/api/saved-searches');
  if (isError(rows)) { box.innerHTML = `<p class="hint left">${esc(rows.detail)}</p>`; return; }
  if (!rows.length) { box.innerHTML = '<p class="hint left">Подписок пока нет. Откройте «Фильтры» в ленте и нажмите «Следить за этим поиском».</p>'; return; }
  box.innerHTML = '';
  rows.forEach((r) => {
    const cat = categoriesCache.find((c) => c.id === r.category_id);
    const parts = [r.q ? `«${r.q}»` : '', cat ? cat.name : '', r.price_max != null ? `до ${r.price_max}` : '', r.city || ''].filter(Boolean);
    const row = document.createElement('div');
    row.className = 'saved-row';
    row.innerHTML = `<span>${esc(parts.join(' · ') || 'Все объявления')}</span><button aria-label="Отключить"><svg class="ic"><use href="#i-trash"/></svg></button>`;
    row.querySelector('button').onclick = async () => {
      const res = await apiDelete(`/api/saved-searches/${r.id}`);
      if (isError(res)) notify(res.detail); else renderSavedSearches();
    };
    box.appendChild(row);
  });
}

/* ---------- История поиска и подсказки ---------- */
const HIST_KEY = 'orient_search_history';
function saveSearchHistory(q) {
  q = (q || '').trim();
  if (q.length < 2) return;
  const h = LS.get(HIST_KEY, []).filter((x) => x.toLowerCase() !== q.toLowerCase());
  h.unshift(q);
  LS.set(HIST_KEY, h.slice(0, 8));
}

function renderSuggest() {
  const box = document.getElementById('searchSuggest');
  const input = document.getElementById('searchInput');
  if (!box || !input) return;
  const v = input.value.trim().toLowerCase();
  const hist = LS.get(HIST_KEY, []).filter((h) => !v || (h.toLowerCase().includes(v) && h.toLowerCase() !== v)).slice(0, 5);
  const cats = v ? categoriesCache.filter((c) => c.name.toLowerCase().includes(v)).slice(0, 3) : [];
  if (!hist.length && !cats.length) { box.classList.add('hidden'); return; }
  box.innerHTML = '';
  if (hist.length) {
    const t = document.createElement('div');
    t.className = 'suggest-title';
    t.innerHTML = '<span>Недавние запросы</span>';
    box.appendChild(t);
    hist.forEach((h) => {
      const b = document.createElement('button');
      b.className = 'suggest-item';
      b.innerHTML = `<svg class="ic"><use href="#i-clock"/></svg><span>${esc(h)}</span>`;
      b.onpointerdown = (e) => { e.preventDefault(); input.value = h; box.classList.add('hidden'); saveSearchHistory(h); loadMarket(true); };
      box.appendChild(b);
    });
  }
  cats.forEach((c) => {
    const b = document.createElement('button');
    b.className = 'suggest-item';
    b.innerHTML = `<svg class="ic"><use href="#i-${catIcon(c.name)}"/></svg><span>Категория: ${esc(c.name)}</span>`;
    b.onpointerdown = (e) => {
      e.preventDefault();
      document.getElementById('categoryFilter').value = String(c.id);
      input.value = '';
      box.classList.add('hidden');
      renderCategoryTiles();
      loadMarket(true);
    };
    box.appendChild(b);
  });
  box.classList.remove('hidden');
}

const _si = document.getElementById('searchInput');
_si?.addEventListener('focus', renderSuggest);
_si?.addEventListener('input', renderSuggest);
_si?.addEventListener('blur', () => { saveSearchHistory(_si.value); setTimeout(() => document.getElementById('searchSuggest')?.classList.add('hidden'), 150); });
_si?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { saveSearchHistory(_si.value); document.getElementById('searchSuggest').classList.add('hidden'); _si.blur(); loadMarket(true); }
});

/* ---------- Недавно смотрели ---------- */
const RECENT_KEY = 'orient_recent';
function pushRecent(item) {
  if (!item || item.is_mine) return;
  const snap = { id: item.id, title: item.title, price: item.price, currency: item.currency, photos: (item.photos || []).slice(0, 1) };
  const list = LS.get(RECENT_KEY, []).filter((x) => x.id !== item.id);
  list.unshift(snap);
  LS.set(RECENT_KEY, list.slice(0, 12));
  renderRecent();
}

function renderRecent() {
  const box = document.getElementById('recentBox');
  const row = document.getElementById('recentRow');
  if (!box || !row) return;
  const list = LS.get(RECENT_KEY, []);
  box.classList.toggle('hidden', !list.length);
  row.innerHTML = '';
  list.forEach((it) => {
    const el = document.createElement('div');
    el.className = 'recent';
    el.innerHTML = `<div class="recent-img">${it.photos && it.photos[0] ? `<img src="${esc(mediaUrl(it.photos[0].file_path))}" alt="" loading="lazy">` : NO_PHOTO_HTML}</div>
      <div class="recent-price">${it.price ? esc(fmtPrice(it.price)) + ' ' + esc(it.currency || '') : 'Договорная'}</div>
      <div class="recent-title">${esc(it.title)}</div>`;
    el.onclick = () => openItemById(it.id);
    row.appendChild(el);
  });
}
document.getElementById('recentClear')?.addEventListener('click', () => { LS.set(RECENT_KEY, []); renderRecent(); });

/* ---------- Бесконечная лента ---------- */
let _sentinelObs = null;
function rearmSentinel() {
  const s = document.getElementById('marketSentinel');
  if (!s || !_sentinelObs) return;
  _sentinelObs.unobserve(s);
  _sentinelObs.observe(s);
}
if ('IntersectionObserver' in window) {
  _sentinelObs = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && marketHasMore && !marketLoading && currentSection() === 'market') loadMarket(false);
  }, { rootMargin: '600px' });
  const s = document.getElementById('marketSentinel');
  if (s) _sentinelObs.observe(s);
}

/* ---------- Просмотр фото ---------- */
function openLightbox(paths, start = 0) {
  if (!paths || !paths.length) return;
  const box = document.getElementById('lightbox');
  const strip = document.getElementById('lbStrip');
  const count = document.getElementById('lbCount');
  strip.innerHTML = '';
  paths.forEach((p) => {
    const slide = document.createElement('div');
    slide.className = 'lb-slide';
    slide.innerHTML = `<img src="${esc(mediaUrl(p))}" alt="">`;
    slide.onclick = () => slide.classList.toggle('zoom'); // касание — приблизить / вернуть
    strip.appendChild(slide);
  });
  const upd = () => { count.textContent = paths.length > 1 ? `${Math.round(strip.scrollLeft / strip.clientWidth) + 1} / ${paths.length}` : ''; };
  strip.onscroll = upd;
  box.classList.remove('hidden');
  strip.scrollLeft = start * strip.clientWidth;
  upd();
}
document.getElementById('lbClose')?.addEventListener('click', () => document.getElementById('lightbox').classList.add('hidden'));

/* ---------- Жалоба ---------- */
let reportTarget = null;
async function openReport(type, id) {
  if (!(await requireRegistered())) return;
  reportTarget = { type, id };
  document.getElementById('reportDetails').value = '';
  document.getElementById('reportModal').classList.remove('hidden');
}
document.getElementById('reportCancel')?.addEventListener('click', () => document.getElementById('reportModal').classList.add('hidden'));
document.getElementById('reportSend')?.addEventListener('click', async () => {
  if (!reportTarget) return;
  const res = await apiPost('/api/reports', {
    target_type: reportTarget.type, target_id: reportTarget.id,
    reason: document.getElementById('reportReason').value,
    details: document.getElementById('reportDetails').value.trim() || null,
  });
  if (isError(res)) { notify(res.detail); return; }
  document.getElementById('reportModal').classList.add('hidden');
  notify(res.duplicate ? 'Вы уже отправляли жалобу — модераторы её рассматривают.' : 'Спасибо! Жалоба отправлена модераторам.');
});

/* ---------- Подсказки при первом запуске ---------- */
let obIndex = 0;
function showOnboarding(done) {
  const root = document.getElementById('onboard');
  const slides = root.querySelectorAll('.ob-slide');
  const dots = root.querySelectorAll('#obDots i');
  obIndex = 0;
  const render = () => {
    slides.forEach((s) => { s.style.transform = `translateX(${-100 * obIndex}%)`; });
    dots.forEach((d, i) => d.classList.toggle('on', i === obIndex));
    document.getElementById('obNext').textContent = obIndex === slides.length - 1 ? 'Начать' : 'Далее';
  };
  const finish = () => {
    root.classList.add('hidden');
    LS.set('orient_onboarded', true);
    if (typeof done === 'function') done();
  };
  document.getElementById('obNext').onclick = () => { if (obIndex >= slides.length - 1) finish(); else { obIndex += 1; render(); } };
  document.getElementById('obSkip').onclick = finish;
  render();
  root.classList.remove('hidden');
}

/* ---------- Конфигурация и ссылки из Telegram ---------- */
let botUsername = null;
let appShortName = null;
async function loadConfig() {
  const res = await apiGet('/api/config');
  if (!isError(res)) { botUsername = res.bot_username || null; appShortName = res.app_short_name || null; }
}

async function openItemById(id) {
  const res = await apiGet(`/api/market/${id}`);
  if (isError(res)) { notify(res.detail || 'Объявление недоступно'); return; }
  openItemDetail(res);
}

/* ?order=ID / ?item=ID (кнопки в сообщениях бота) и startapp=order_ID / item_ID */
function handleDeepLink() {
  const q = new URLSearchParams(location.search);
  let order = q.get('order'); let item = q.get('item'); let shop = q.get('shop');
  // start_param приходит из прямой ссылки приложения (t.me/бот/имя?startapp=item_5); запасной путь — хеш адреса.
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  const param = tg?.initDataUnsafe?.start_param || hash.get('tgWebAppStartParam') || q.get('startapp') || '';
  const m = /^(order|item|shop)_(\d+)$/.exec(param);
  if (m) { if (m[1] === 'order') order = m[2]; else if (m[1] === 'shop') shop = m[2]; else item = m[2]; }
  if (q.get('tab') === 'myads') { navigate('myads'); return; }
  const chat = /^(\d+)-(\d+)$/.exec(q.get('chat') || '');
  if (chat) { openChat(Number(chat[1]), Number(chat[2])); return; }
  if (order && /^\d+$/.test(order)) { navigate('orders'); openOrderChat(Number(order)); }
  else if (item && /^\d+$/.test(item)) openItemById(Number(item));
  else if (shop && /^\d+$/.test(shop)) openShopItems(Number(shop));
}

/* ---------- Настройки ---------- */
document.getElementById('clearHistoryBtn')?.addEventListener('click', () => { LS.set(HIST_KEY, []); notify('История поиска очищена'); });
document.getElementById('showTourBtn')?.addEventListener('click', () => showOnboarding());

/* ---------- Заставка ---------- */
function hideSplash() {
  const s = document.getElementById('splash');
  if (!s) return;
  s.classList.add('out');
  setTimeout(() => s.remove(), 600);
}
