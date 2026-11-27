/* ===== Бренды (фильтр и автозаполнение), чат с продавцом, подписка на магазин, профиль продавца,
   сравнение товаров, «Поделиться», синхронизация избранного ===== */

/* ---------- Бренды в фильтре ---------- */
let fBrandSet = new Set();
let _brandTimer = null;

function initBrandPanel() {
  fBrandSet = new Set(filters.brand);
  document.getElementById('fBrandSearch').value = '';
  document.getElementById('fBrandPanel').classList.add('hidden');
  document.getElementById('fBrandToggle').classList.remove('open');
  updateBrandCount();
}

function updateBrandCount() {
  const b = document.getElementById('fBrandSel');
  b.textContent = fBrandSet.size;
  b.classList.toggle('hidden', !fBrandSet.size);
}

async function loadBrandList() {
  const list = document.getElementById('fBrandList');
  const q = document.getElementById('fBrandSearch').value.trim();
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  const cat = document.getElementById('categoryFilter').value;
  if (cat) params.set('category_id', cat);
  const res = await apiGet('/api/market/brands?' + params.toString());
  if (isError(res)) { list.innerHTML = `<p class="hint left">${esc(res.detail)}</p>`; return; }
  // выбранные бренды, которых нет в выдаче (например, при поиске), остаются в списке сверху
  const names = new Set(res.map((r) => r.name));
  const extra = [...fBrandSet].filter((n) => !names.has(n) && (!q || n.toLowerCase().includes(q.toLowerCase())))
    .map((n) => ({ name: n, count: 0 }));
  const rows = [...extra, ...res];
  if (!rows.length) {
    list.innerHTML = `<p class="hint left">${q ? 'Такого бренда нет в объявлениях' : 'Пока ни один продавец не указал бренд'}</p>`;
    return;
  }
  list.innerHTML = '';
  rows.forEach((r) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'brand-row' + (fBrandSet.has(r.name) ? ' on' : '');
    b.innerHTML = `<span class="box"><svg class="ic"><use href="#i-check"/></svg></span><span class="nm">${esc(r.name)}</span><span class="cnt">(${r.count})</span>`;
    b.onclick = () => {
      if (fBrandSet.has(r.name)) fBrandSet.delete(r.name); else fBrandSet.add(r.name);
      b.classList.toggle('on', fBrandSet.has(r.name));
      updateBrandCount();
    };
    list.appendChild(b);
  });
}

document.getElementById('fBrandToggle')?.addEventListener('click', (e) => {
  const panel = document.getElementById('fBrandPanel');
  const open = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !open);
  e.currentTarget.classList.toggle('open', open);
  if (open) loadBrandList();
});
document.getElementById('fBrandSearch')?.addEventListener('input', () => {
  clearTimeout(_brandTimer);
  _brandTimer = setTimeout(loadBrandList, 250);
});

/* ---------- Автозаполнение бренда у продавца ---------- */
let _acTimer = null;
let _acSeq = 0;
function hideBrandSuggest() { document.getElementById('itemBrandSuggest')?.classList.add('hidden'); }

async function showBrandSuggest() {
  const input = document.getElementById('itemBrand');
  const box = document.getElementById('itemBrandSuggest');
  const q = input.value.trim();
  if (!q) { hideBrandSuggest(); return; }
  const seq = ++_acSeq;
  const res = await apiGet('/api/market/brands/suggest?q=' + encodeURIComponent(q));
  if (seq !== _acSeq || isError(res)) return;
  const exact = res.some((n) => n.toLowerCase() === q.toLowerCase());
  if (!res.length || (exact && res.length === 1)) { hideBrandSuggest(); return; }
  box.innerHTML = '';
  res.forEach((name) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'suggest-item';
    const i = name.toLowerCase().indexOf(q.toLowerCase());
    b.innerHTML = i >= 0
      ? `<span>${esc(name.slice(0, i))}<b>${esc(name.slice(i, i + q.length))}</b>${esc(name.slice(i + q.length))}</span>`
      : `<span>${esc(name)}</span>`;
    b.onpointerdown = (e) => { e.preventDefault(); input.value = name; hideBrandSuggest(); };
    box.appendChild(b);
  });
  box.classList.remove('hidden');
}
document.getElementById('itemBrand')?.addEventListener('input', () => { clearTimeout(_acTimer); _acTimer = setTimeout(showBrandSuggest, 150); });
document.getElementById('itemBrand')?.addEventListener('focus', showBrandSuggest);
document.getElementById('itemBrand')?.addEventListener('blur', () => setTimeout(hideBrandSuggest, 150));

/* ---------- Профиль продавца ---------- */
function monthYear(iso) {
  if (!iso) return '';
  const d = new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : iso + 'Z');
  return isNaN(d) ? '' : `${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;
}

function sellerLine(st, noRating = false) {
  const bits = [];
  if (st.rating != null && !noRating) bits.push(`★ ${st.rating} (${st.reviews})`);
  bits.push(`Продаж: ${st.sales || 0}`);
  if (st.since) bits.push(`на Orient с ${monthYear(st.since)}`);
  return bits.join(' · ');
}

async function loadSellerCard(item, box) {
  if (!box) return;
  const st = await apiGet(`/api/market/${item.id}/seller`);
  if (isError(st) || detailItem !== item) return;
  const avatar = st.logo ? `<img src="${esc(mediaUrl(st.logo))}" alt="">` : `<span>${esc((st.name || '?').slice(0, 1).toUpperCase())}</span>`;
  box.innerHTML = `<div class="sc-ava">${avatar}</div>
    <div class="sc-info"><div class="sc-name">${esc(st.name)}</div><div class="sc-line">${esc(sellerLine(st))}</div>
    ${st.fast_reply ? '<span class="badge fast"><svg class="ic sm"><use href="#i-bolt"/></svg>Отвечает быстро</span>' : ''}</div>
    ${st.is_shop ? '<svg class="ic sc-go"><use href="#i-arrow"/></svg>' : ''}`;
  if (st.is_shop && st.shop_id) box.onclick = () => openShopDetailModal(st.shop_id);
  box.classList.remove('hidden');
}

/* ---------- Подписка на магазин ---------- */
function renderShopSocial(box, shop, data) {
  if (!box) return;
  const st = data.stats || {};
  const staff = !!(myShop && myShop.id === shop.id);
  box.innerHTML = `<p class="meta">${esc(sellerLine(st, true))}${st.followers ? ` · подписчиков: ${st.followers}` : ''}</p>
    ${st.fast_reply ? '<span class="badge fast"><svg class="ic sm"><use href="#i-bolt"/></svg>Отвечает быстро</span>' : ''}`;
  if (staff) return;
  const btn = document.createElement('button');
  btn.className = 'pill-btn mt';
  let on = !!data.following;
  const paint = () => {
    btn.classList.toggle('on', on);
    btn.innerHTML = `<svg class="ic"><use href="#i-bell"/></svg>${on ? 'Вы подписаны' : 'Подписаться на новинки'}`;
  };
  paint();
  btn.onclick = async () => {
    if (!(await requireRegistered())) return;
    const res = await apiPost(`/api/shops/${shop.id}/follow`, { on: !on });
    if (isError(res)) { notify(res.detail); return; }
    on = !on;
    paint();
    if (on) notify('Готово! Бот напишет, когда в магазине появится новый товар.');
  };
  box.appendChild(btn);
}

/* ---------- Чат с продавцом ---------- */
let chatCtx = null;
let chatPoll = null;

function chatBubble(m) {
  const d = document.createElement('div');
  d.className = 'bubble ' + (m.mine ? 'mine' : 'their');
  d.innerHTML = `${esc(m.text)}<time>${esc(fmtDate(m.created_at).slice(-5))}</time>`;
  return d;
}

function renderChat(data, stick = true) {
  const body = document.getElementById('chatBody');
  const atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
  body.innerHTML = '';
  if (!data.messages.length) body.innerHTML = '<p class="hint">Напишите первое сообщение — продавец получит уведомление от бота.</p>';
  data.messages.forEach((m) => body.appendChild(chatBubble(m)));
  if (stick || atEnd) body.scrollTop = body.scrollHeight;
  chatCtx.last = data.messages.length ? data.messages[data.messages.length - 1].id : 0;
}

function chatUrl() { return `/api/chats/${chatCtx.itemId}${chatCtx.buyer ? `?buyer=${chatCtx.buyer}` : ''}`; }

async function openChat(itemId, buyer = null) {
  if (!(await requireRegistered())) return;
  chatCtx = { itemId, buyer, last: 0 };
  const modal = document.getElementById('chatModal');
  document.getElementById('chatBody').innerHTML = '<p class="hint">Загрузка...</p>';
  document.getElementById('chatHead').innerHTML = '';
  document.getElementById('chatText').value = '';
  modal.classList.remove('hidden');
  const data = await apiGet(chatUrl());
  if (isError(data)) { document.getElementById('chatBody').innerHTML = `<p class="hint">${esc(data.detail)}</p>`; return; }
  chatCtx.buyer = data.role === 'seller' ? data.buyer : null;
  const it = data.item;
  const head = document.getElementById('chatHead');
  head.innerHTML = `<div class="ch-img">${it.photo ? `<img src="${esc(mediaUrl(it.photo))}" alt="">` : ''}</div>
    <div class="ch-t"><b>${esc(it.title)}</b><span>${it.price ? esc(fmtPrice(it.price)) + ' ' + esc(it.currency || '') : 'Договорная'}</span></div>`;
  head.onclick = () => openItemById(it.id);
  renderChat(data);
  clearInterval(chatPoll);
  chatPoll = setInterval(async () => {
    if (modal.classList.contains('hidden') || !chatCtx) { clearInterval(chatPoll); return; }
    const d = await apiGet(chatUrl());
    if (isError(d)) return;
    const lastId = d.messages.length ? d.messages[d.messages.length - 1].id : 0;
    if (lastId !== chatCtx.last) renderChat(d, false);
  }, 7000);
}

function closeChat() {
  document.getElementById('chatModal').classList.add('hidden');
  clearInterval(chatPoll);
  chatCtx = null;
}

async function sendChat() {
  if (!chatCtx) return;
  const input = document.getElementById('chatText');
  const text = input.value.trim();
  if (!text) return;
  const btn = document.getElementById('chatSend');
  btn.disabled = true;
  const res = await apiPost(chatUrl(), { text });
  btn.disabled = false;
  if (isError(res)) { notify(res.detail); return; }
  input.value = '';
  input.style.height = '';
  const body = document.getElementById('chatBody');
  if (body.querySelector('.hint')) body.innerHTML = '';
  body.appendChild(chatBubble(res.message));
  chatCtx.last = res.message.id;
  body.scrollTop = body.scrollHeight;
}

document.getElementById('chatBack')?.addEventListener('click', closeChat);
document.getElementById('chatSend')?.addEventListener('click', sendChat);
document.getElementById('chatText')?.addEventListener('input', (e) => {
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
});

async function openInbox() {
  if (!(await requireRegistered())) return;
  closeDrawer();
  const modal = document.getElementById('inboxModal');
  const list = document.getElementById('inboxList');
  list.innerHTML = '<p class="hint">Загрузка...</p>';
  modal.classList.remove('hidden');
  const rows = await apiGet('/api/chats');
  if (isError(rows)) { list.innerHTML = `<p class="hint">${esc(rows.detail)}</p>`; return; }
  if (!rows.length) { list.innerHTML = emptyState('Переписок пока нет. Нажмите «Чат» в карточке товара.'); return; }
  list.innerHTML = '';
  rows.forEach((r) => {
    const row = document.createElement('button');
    row.className = 'inbox-row';
    row.innerHTML = `<div class="ch-img">${r.photo ? `<img src="${esc(mediaUrl(r.photo))}" alt="">` : ''}</div>
      <div class="ir-body"><div class="ir-top"><b>${esc(r.with)}</b><time>${esc(fmtDate(r.last_at).slice(0, 10))}</time></div>
      <div class="ir-item">${esc(r.title)}${r.role === 'seller' ? ' · вы продавец' : ''}</div>
      <div class="ir-last">${r.last_mine ? 'Вы: ' : ''}${esc(r.last_text)}</div></div>`;
    row.onclick = () => { modal.classList.add('hidden'); openChat(r.item_id, r.role === 'seller' ? r.buyer : null); };
    list.appendChild(row);
  });
}
document.getElementById('inboxBack')?.addEventListener('click', () => document.getElementById('inboxModal').classList.add('hidden'));
document.getElementById('drawerChats')?.addEventListener('click', openInbox);

/* ---------- Сравнение (до 2 товаров, хранится на устройстве) ---------- */
const CMP_KEY = 'orient_compare';
const cmpGet = () => LS.get(CMP_KEY, []);

function cmpInList(id) { return cmpGet().some((x) => x.id === id); }

function refreshCompareBtn(item) {
  const btn = document.getElementById('detailCompare');
  const n = cmpGet().length;
  const c = document.getElementById('drawerCompareCount');
  if (c) { c.textContent = n; c.classList.toggle('hidden', !n); }
  if (!btn) return;
  const it = item || detailItem;
  if (!it) return;
  const on = cmpInList(it.id);
  btn.classList.toggle('on', on);
  btn.innerHTML = `<svg class="ic"><use href="#i-compare"/></svg>${on ? 'Убрать из сравнения' : (n === 1 ? 'Сравнить с выбранным' : 'Добавить к сравнению')}`;
  btn.onclick = () => toggleCompare(it);
}

function toggleCompare(item) {
  let list = cmpGet();
  if (cmpInList(item.id)) list = list.filter((x) => x.id !== item.id);
  else { list.push(item); if (list.length > 2) list = list.slice(-2); }
  LS.set(CMP_KEY, list);
  refreshCompareBtn(item);
  if (list.length === 2 && cmpInList(item.id)) openCompare();
}

async function openCompare() {
  closeDrawer();
  const list = cmpGet();
  const body = document.getElementById('compareBody');
  document.getElementById('compareModal').classList.remove('hidden');
  if (!list.length) { body.innerHTML = '<p class="hint">Добавьте товары кнопкой «Добавить к сравнению» в карточке.</p>'; return; }
  body.innerHTML = '<p class="hint">Загрузка...</p>';
  const fresh = await Promise.all(list.map(async (x) => { const r = await apiGet(`/api/market/${x.id}`); return isError(r) ? x : r; }));
  const rows = [
    ['Цена', (i) => priceText(i)],
    ['Бренд', (i) => esc(i.brand || '—')],
    ['Состояние', (i) => esc(CONDITION_LABELS[i.condition] || '—')],
    ['Размеры', (i) => esc((i.sizes || '').split(',').filter(Boolean).join(', ') || '—')],
    ['Цвета', (i) => esc((i.colors || '').split(',').filter(Boolean).join(', ') || '—')],
    ['Город', (i) => esc(i.city || '—')],
    ['Продавец', (i) => esc(i.shop_id ? (i.shop?.name || 'Магазин') : 'Частное лицо')],
  ];
  const cell = (i) => `<div class="cmp-head"><div class="cmp-img">${i.photos && i.photos[0] ? `<img src="${esc(mediaUrl(i.photos[0].file_path))}" alt="">` : ''}</div><div class="cmp-title">${esc(i.title)}</div></div>`;
  body.innerHTML = `<div class="cmp" style="--cols:${fresh.length}">
      ${fresh.map(cell).join('')}
      ${rows.map(([label, fn]) => `<div class="cmp-label" style="grid-column:1/-1">${label}</div>${fresh.map((i) => `<div class="cmp-val">${fn(i)}</div>`).join('')}`).join('')}
    </div>`;
  body.querySelectorAll('.cmp-head').forEach((h, k) => { h.onclick = () => { document.getElementById('compareModal').classList.add('hidden'); openItemDetail(fresh[k]); }; });
}
document.getElementById('compareClose')?.addEventListener('click', () => document.getElementById('compareModal').classList.add('hidden'));
document.getElementById('compareClear')?.addEventListener('click', () => { LS.set(CMP_KEY, []); document.getElementById('compareModal').classList.add('hidden'); refreshCompareBtn(); });
document.getElementById('drawerCompare')?.addEventListener('click', openCompare);

/* ---------- Поделиться ---------- */
let shareItem = null; // { kind: 'item' | 'shop', id, title, text }
function openShare(item) {
  shareItem = { kind: 'item', id: item.id, title: item.title,
    text: `${item.title} — ${item.price ? item.price + ' ' + (item.currency || '') : 'Договорная'}` };
  showShareModal();
}
function openShopShare(shop) {
  shareItem = { kind: 'shop', id: shop.id, title: shop.name, text: `Магазин «${shop.name}» в Orient` };
  showShareModal();
}
function showShareModal() {
  const isItem = shareItem.kind === 'item';
  document.getElementById('shareTitle').textContent = isItem ? 'Поделиться товаром' : 'Поделиться магазином';
  document.getElementById('shareCard').classList.toggle('hidden', !isItem);
  document.getElementById('shareCardHint').classList.toggle('hidden', !isItem);
  document.getElementById('shareModal').classList.remove('hidden');
}
const closeShare = () => document.getElementById('shareModal').classList.add('hidden');

/* Ссылка ведёт именно на объявление/магазин: с коротким именем приложения открывается сразу,
   иначе через бота (/start item_5), и его кнопка открывает нужную страницу. */
function shareUrl(t) {
  if (!botUsername) return location.origin;
  if (appShortName) return `https://t.me/${botUsername}/${appShortName}?startapp=${t.kind}_${t.id}`;
  return `https://t.me/${botUsername}?start=${t.kind}_${t.id}`;
}

document.getElementById('shareCancel')?.addEventListener('click', closeShare);
document.getElementById('shareModal')?.addEventListener('click', (e) => { if (e.target.id === 'shareModal') closeShare(); });
document.getElementById('shareCard')?.addEventListener('click', async () => {
  if (!shareItem || shareItem.kind !== 'item') return;
  if (!(await requireRegistered())) return;
  const res = await apiPost(`/api/market/${shareItem.id}/share`, {});
  closeShare();
  notify(isError(res) ? res.detail : 'Карточка отправлена в чат с ботом — перешлите её друзьям.');
});
document.getElementById('shareTg')?.addEventListener('click', () => {
  if (!shareItem) return;
  const link = `https://t.me/share/url?url=${encodeURIComponent(shareUrl(shareItem))}&text=${encodeURIComponent(shareItem.text)}`;
  closeShare();
  if (tg && tg.openTelegramLink) tg.openTelegramLink(link); else window.open(link, '_blank');
});
document.getElementById('shareCopy')?.addEventListener('click', async () => {
  if (!shareItem) return;
  const url = shareUrl(shareItem);
  closeShare();
  try { await navigator.clipboard.writeText(url); notify('Ссылка скопирована'); } catch (e) { notify(url); }
});
/* Кнопки «Поделиться магазином» (карточка своего магазина, страница магазина) */
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-share-shop]');
  if (b) openShopShare({ id: Number(b.dataset.shareShop), name: b.dataset.shopName || 'Магазин' });
});

/* ---------- Избранное: синхронизация с сервером ---------- */
async function syncFavs() {
  if (!me.loaded || !me.is_registered) return;
  const ids = await apiGet('/api/market/favs/mine');
  if (isError(ids)) return;
  const server = new Set(ids);
  // 1) на сервере есть, на устройстве нет (новый телефон) — подгружаем
  const missing = ids.filter((id) => !isFav(id)).slice(0, 30);
  for (const id of missing) {
    const it = await apiGet(`/api/market/${id}`);
    if (!isError(it)) favs[id] = it;
  }
  // 2) на устройстве есть, на сервере нет (добавлено до обновления) — отправляем
  Object.keys(favs).forEach((id) => {
    if (!server.has(Number(id)) && !favs[id].is_mine) apiPost(`/api/market/${id}/fav`, { on: true });
  });
  if (missing.length) { saveFavs(); refreshFavUi(); if (currentSection() === 'favs') renderFavs(); }
}

refreshCompareBtn();
