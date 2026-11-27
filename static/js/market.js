const PAGE_SIZE = 30;
let categoriesCache = [];
let marketOffset = 0;
let marketReqId = 0;
let marketHasMore = false;
let marketLoading = false;

/* Иконки категорий в цвет бренда (вместо цветных эмодзи) — подбираются по названию. */
function catIcon(name) {
  const n = String(name || '').toLowerCase();
  if (!n || n === 'все') return 'hanger';
  if (/худи|толстов/.test(n)) return 'hoodie';
  if (/футбол|майк|рубаш|топ/.test(n)) return 'shirt';
  if (/куртк|пальто|пуховик|верхн|плащ/.test(n)) return 'jacket';
  if (/кофт|свитер|джемпер|кардиган|трикот/.test(n)) return 'sweater';
  if (/брюк|джинс|штан|шорт/.test(n)) return 'pants';
  if (/обув|кроссов|ботин|туфл|кед/.test(n)) return 'shoe';
  if (/аксесс|часы|ремн|очки|украш/.test(n)) return 'watch';
  if (/сумк|рюкзак|кошел/.test(n)) return 'handbag';
  if (/одежд/.test(n)) return 'shirt';
  return 'hanger';
}

/* ---------- Категории (плитки на главной + форма объявления) ---------- */
function renderCategoryTiles() {
  const box = document.getElementById('categoryTiles');
  if (!box) return;
  const cur = document.getElementById('categoryFilter')?.value || '';
  const all = [{ id: '', name: 'Все', icon: '' }, ...categoriesCache];
  box.innerHTML = all.map((c) => `
    <button class="cat ${String(c.id) === cur ? 'active' : ''}" data-cat="${esc(c.id)}">
      <span class="cat-ic"><svg class="ic"><use href="#i-${catIcon(c.name)}"/></svg></span>
      <span class="cat-name">${esc(c.name)}</span>
    </button>`).join('');
  box.querySelectorAll('.cat').forEach((btn) => {
    btn.onclick = () => {
      document.getElementById('categoryFilter').value = btn.dataset.cat;
      renderCategoryTiles();
      loadMarket(true);
    };
  });
}

async function loadCategories() {
  const res = await apiGet('/api/categories');
  categoriesCache = Array.isArray(res) ? res : [];
  const opts = categoriesCache.map((c) => `<option value="${c.id}">${esc(c.icon || '')} ${esc(c.name)}</option>`).join('');
  const filter = document.getElementById('categoryFilter');
  if (filter) {
    const keep = filter.value;
    filter.innerHTML = '<option value="">Все категории</option>' + opts;
    filter.value = keep;
  }
  const form = document.getElementById('itemCategory');
  if (form) form.innerHTML = '<option value="">Без категории</option>' + opts;
  renderCategoryTiles();
}

/* ---------- Карточка объявления ---------- */
function deliveryBadges(item) {
  const m = item.delivery_methods || '';
  const parts = [];
  if (m.includes('pickup')) parts.push('Самовывоз');
  if (m.includes('post')) parts.push('Почта');
  return parts.map((p) => `<span class="badge">${p}</span>`).join('');
}

function fmtPrice(v) {
  const t = String(v).trim();
  return /^\d+([.,]\d+)?$/.test(t) ? t.replace(/^(\d+)/, (m) => m.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00A0')) : t;
}

function priceText(item) {
  if (!item.price) return 'Договорная';
  const old = item.old_price ? ` <s class="old-price">${esc(fmtPrice(item.old_price))}</s>` : '';
  return `${esc(fmtPrice(item.price))} ${esc(item.currency || '')}${old}`;
}

const NO_PHOTO_HTML = '<div class="no-photo"><svg class="ic" style="fill:currentColor;stroke:none"><use href="#i-star"/></svg></div>';

/* Кнопки действий в листе товара. getSel() проверяет выбор размера/цвета и возвращает {size,color} либо null. */
function buildItemActions(item, container, getSel) {
  container.innerHTML = '';
  if (item.sold) { container.innerHTML = '<span class="hint left">Этот товар уже продан</span>'; return; }
  if (item.is_mine) {
    const st = item.moderation_status || 'approved';
    container.innerHTML = st === 'pending'
      ? '<span class="hint left">⏳ Объявление на модерации — пока видно только вам</span>'
      : (st === 'rejected'
        ? `<span class="hint left">❌ Отклонено модератором${item.reject_reason ? ': ' + esc(item.reject_reason) : ''}. Исправьте его в «Моих объявлениях».</span>`
        : '<span class="hint left">Это ваше объявление</span>');
    return;
  }
  if (item.shop_id) {
    const buy = document.createElement('button');
    buy.className = 'primary';
    buy.textContent = 'Купить';
    buy.onclick = () => { const sel = getSel(); if (sel) { countContact(item.id); buyItem(item.shop_id, item.id, sel); } };
    container.appendChild(buy);
    const add = document.createElement('button');
    add.className = 'secondary';
    add.textContent = 'В корзину';
    add.onclick = () => { const sel = getSel(); if (sel) cartAdd(item, sel); };
    container.appendChild(add);
  }
  const chat = document.createElement('button');
  chat.className = 'secondary';
  chat.innerHTML = '<svg class="ic"><use href="#i-chat"/></svg>Чат';
  chat.onclick = () => { countContact(item.id); openChat(item.id); };
  container.appendChild(chat);
  if (item.seller_username) {
    const msg = document.createElement('button');
    msg.className = 'secondary';
    msg.textContent = 'В Telegram';
    msg.onclick = () => { countContact(item.id); openTelegramUser(item.seller_username); };
    container.appendChild(msg);
  }
  if (item.phone && item.contact_mode === 'calls') {
    const call = document.createElement('a');
    call.className = 'btn secondary';
    call.href = 'tel:' + item.phone.replace(/[^\d+]/g, '');
    call.textContent = 'Позвонить';
    call.onclick = () => countContact(item.id);
    container.appendChild(call);
  }
  if (!container.children.length) container.innerHTML = '<span class="hint left">Контакты не указаны</span>';
}

/* Карточка в «Моих объявлениях»: статистика и управление. */
function buildMineCard(item) {
  const div = document.createElement('div');
  div.className = 'tile mine';
  const photo = item.photos && item.photos.length
    ? `<img src="${esc(mediaUrl(item.photos[0].file_path))}" alt="" loading="lazy">` : NO_PHOTO_HTML;
  const where = item.shop_id ? (item.shop?.name || 'Магазин') : (item.city || 'Беларусь');
  const wait = item.bump_wait || 0;
  const bumpLabel = wait ? `Поднять · ${Math.ceil(wait / 3600)} ч` : 'Поднять';
  const st = item.moderation_status || 'approved';
  const published = st === 'approved';
  const modBadge = st === 'pending'
    ? '<span class="mod-badge pending">⏳ На модерации</span>'
    : (st === 'rejected' ? '<span class="mod-badge rejected">❌ Отклонено</span>' : '');
  const modReason = st === 'rejected'
    ? `<div class="mod-reason">${item.reject_reason ? 'Причина: ' + esc(item.reject_reason) : 'Исправьте объявление и отправьте повторно.'}</div>`
    : (st === 'pending' ? '<div class="mod-reason soft">Увидят все после проверки модератором</div>' : '');
  div.innerHTML = `
    <div class="tile-img">${photo}${item.sold ? '<div class="sold-mark">Продано</div>' : ''}</div>
    <div class="tile-body">
      ${modBadge}
      <div class="tile-title">${esc(item.title)}</div>
      <div class="tile-price">${priceText(item)}</div>
      <div class="tile-meta">${esc(where)}</div>
      ${modReason}
      <div class="stats">
        <span title="Просмотры"><svg class="ic"><use href="#i-eye"/></svg>${item.views || 0}</span>
        <span title="В избранном"><svg class="ic"><use href="#i-heart"/></svg>${item.favs || 0}</span>
        <span title="Обращения"><svg class="ic"><use href="#i-chat"/></svg>${item.contacts || 0}</span>
      </div>
      <div class="mine-actions"></div>
    </div>`;
  const box = div.querySelector('.mine-actions');
  const mk = (cls, icon, text, fn, disabled = false) => {
    const b = document.createElement('button');
    b.className = cls;
    b.disabled = disabled;
    b.innerHTML = `<svg class="ic"><use href="#i-${icon}"/></svg>${text}`;
    b.onclick = (e) => { e.stopPropagation(); fn(); };
    box.appendChild(b);
  };
  if (!published) {
    // На модерации или отклонено: поднимать и отмечать проданным рано, можно исправить и отправить заново.
    mk('secondary', 'sliders', st === 'rejected' ? 'Исправить' : 'Изменить', () => openItemModal({ item }));
  } else if (!item.sold) {
    mk('secondary', 'up', bumpLabel, () => bumpItem(item), wait > 0);
    mk('secondary', 'check', 'Продано', () => setSold(item, true));
    mk('secondary', 'sliders', 'Изменить', () => openItemModal({ item }));
  } else {
    mk('secondary', 'up', 'Вернуть в продажу', () => setSold(item, false));
  }
  mk('secondary', 'copy', 'Копия', () => openItemModal({ item, duplicate: true }));
  mk('secondary danger', 'trash', 'Удалить', () => deleteItem(item));
  div.onclick = () => openItemDetail(item);
  return div;
}

async function bumpItem(item) {
  const res = await apiPost(`/api/market/${item.id}/bump`, {});
  if (isError(res)) { notify(res.detail); return; }
  notify('Объявление поднято наверх ленты');
  loadMyAds();
}

async function setSold(item, sold) {
  if (sold && !(await askConfirm('Отметить как проданное? Оно исчезнет из ленты, но останется в архиве — его можно вернуть.'))) return;
  const res = await apiPost(`/api/market/${item.id}/sold`, { sold });
  if (isError(res)) { notify(res.detail); return; }
  loadMyAds();
}

function buildItemCard(item, opts = {}) {
  if (opts.mine) return buildMineCard(item);
  const div = document.createElement('div');
  div.className = 'tile';
  const photo = item.photos && item.photos.length
    ? `<img src="${esc(mediaUrl(item.photos[0].file_path))}" alt="" loading="lazy">`
    : NO_PHOTO_HTML;
  const where = item.shop_id ? (item.shop?.name || 'Магазин') : (item.city || 'Беларусь');
  div.innerHTML = `
    <div class="tile-img">${photo}
      ${item.photos && item.photos.length > 1 ? `<span class="photo-count"><svg class="ic"><use href="#i-camera"/></svg>${item.photos.length}</span>` : ''}
      <button class="heart ${isFav(item.id) ? 'on' : ''}" data-fav-id="${esc(item.id)}" aria-label="В избранное"><svg class="ic"><use href="#i-heart"/></svg></button>
    </div>
    <div class="tile-body">
      <div class="tile-title">${esc(item.title)}</div>
      <div class="tile-price">${priceText(item)}</div>
      <div class="tile-meta">${item.shop_id ? '<svg class="ic sm"><use href="#i-store"/></svg>' : ''}${esc(where)}</div>
    </div>`;
  div.querySelector('.heart').onclick = (e) => { e.stopPropagation(); toggleFav(item); };
  div.onclick = () => openItemDetail(item);
  return div;
}

/* ---------- Лист товара (как «страница товара» в макете) ---------- */
let detailItem = null;

function optionsBlock(label, list, onPick) {
  if (!list.length) return '';
  return `<div class="opt-label">${label}</div><div class="opts" data-opts="${label}">${list.map((o) => `<button class="opt" data-v="${esc(o)}">${esc(o)}</button>`).join('')}</div>`;
}

function openItemDetail(item) {
  detailItem = item;
  const modal = document.getElementById('itemDetailModal');
  const body = document.getElementById('itemDetailBody');
  const photos = item.photos || [];
  const gallery = photos.length
    ? `<div class="gallery-strip">${photos.map((p) => `<img src="${esc(mediaUrl(p.file_path))}" alt="">`).join('')}</div>
       ${photos.length > 1 ? `<div class="dots">${photos.map((_, i) => `<i class="${i ? '' : 'on'}"></i>`).join('')}</div>` : ''}`
    : NO_PHOTO_HTML;
  const shopBadge = item.shop_id
    ? `<span class="badge green"><svg class="ic sm"><use href="#i-store"/></svg>${esc(item.shop?.name || 'Магазин')}</span>`
    : '<span class="badge">Частное</span>';
  const phoneLine = item.phone
    ? `<p class="meta">📱 ${esc(item.phone)} · ${item.contact_mode === 'calls' ? 'сообщения и звонки' : 'только сообщения'}</p>` : '';
  const pickup = item.pickup_address ? `<p class="meta">📍 Самовывоз: ${esc(item.pickup_address)}</p>` : '';
  const sizes = (item.sizes || '').split(',').filter(Boolean);
  const colors = (item.colors || '').split(',').filter(Boolean);

  body.innerHTML = `
    <div class="gallery">${gallery}</div>
    <div class="detail-panel">
      ${item.sold ? '<span class="sold-badge">Продано</span>' : ''}
      <div class="detail-title">${esc(item.title)}</div>
      <div class="detail-price">${priceText(item)}</div>
      <div class="badges">${shopBadge}${item.brand ? `<span class="badge">${esc(item.brand)}</span>` : ''}${item.condition ? `<span class="badge cond">${esc(CONDITION_LABELS[item.condition] || '')}</span>` : ''}${deliveryBadges(item)}</div>
      <div class="seller-card hidden"></div>
      ${item.sold ? '' : optionsBlock('Размер', sizes) + optionsBlock('Цвет', colors)}
      ${item.description ? `<p class="desc">${esc(item.description)}</p>` : ''}
      <p class="meta">🇧🇾 ${item.city ? esc(item.city) : 'Беларусь'} · <svg class="ic sm"><use href="#i-eye"/></svg>${item.views || 0}</p>
      ${pickup}${phoneLine}
      <div class="actions-row"></div>
      <button id="detailCompare" class="pill-btn mt" type="button"></button>
    </div>
    <div class="similar hidden"><h3>Похожие объявления</h3><div class="market-grid"></div></div>`;

  const sel = { Размер: null, Цвет: null };
  body.querySelectorAll('.opts').forEach((box) => {
    box.querySelectorAll('.opt').forEach((b) => {
      b.onclick = () => {
        sel[box.dataset.opts] = b.dataset.v;
        box.querySelectorAll('.opt').forEach((x) => x.classList.toggle('active', x === b));
      };
    });
  });
  const getSel = () => {
    if (sizes.length && !sel['Размер']) { notify('Выберите размер'); return null; }
    if (colors.length && !sel['Цвет']) { notify('Выберите цвет'); return null; }
    return { size: sel['Размер'], color: sel['Цвет'] };
  };
  buildItemActions(item, body.querySelector('.actions-row'), getSel);

  const strip = body.querySelector('.gallery-strip');
  if (strip) {
    strip.addEventListener('scroll', () => {
      const i = Math.round(strip.scrollLeft / strip.clientWidth);
      body.querySelectorAll('.dots i').forEach((d, k) => d.classList.toggle('on', k === i));
    }, { passive: true });
    strip.querySelectorAll('img').forEach((img, i) => {
      img.onclick = () => openLightbox(photos.map((p) => p.file_path), i);
    });
  }
  const fav = document.getElementById('itemDetailFav');
  fav.dataset.favId = item.id;
  fav.classList.toggle('on', isFav(item.id));
  fav.classList.toggle('hidden', !!item.is_mine);
  document.getElementById('itemDetailReport').classList.toggle('hidden', !!item.is_mine);
  modal.classList.remove('hidden');
  modal.scrollTop = 0;

  if (!item.is_mine) { countView(item.id); pushRecent(item); }
  loadSellerCard(item, body.querySelector('.seller-card'));
  refreshCompareBtn(item);
  loadSimilar(item, body.querySelector('.similar'));
}

async function loadSimilar(item, box) {
  if (!item.category_id || item.sold) return;
  const res = await apiGet(`/api/market?category_id=${item.category_id}&limit=7`);
  if (isError(res) || detailItem !== item) return;
  const others = res.filter((x) => x.id !== item.id).slice(0, 6);
  if (!others.length) return;
  const grid = box.querySelector('.market-grid');
  others.forEach((x) => {
    const card = buildItemCard(x);
    card.onclick = () => openItemDetail(x);
    grid.appendChild(card);
  });
  box.classList.remove('hidden');
}

document.getElementById('itemDetailBack')?.addEventListener('click', () => document.getElementById('itemDetailModal').classList.add('hidden'));
document.getElementById('itemDetailFav')?.addEventListener('click', () => { if (detailItem) toggleFav(detailItem); });
document.getElementById('itemDetailReport')?.addEventListener('click', () => { if (detailItem) openReport('item', detailItem.id); });
document.getElementById('itemDetailShare')?.addEventListener('click', () => { if (detailItem) openShare(detailItem); });

function renderItemsInto(list, items, append = false) {
  if (!list) return;
  if (!append) list.innerHTML = '';
  if (!Array.isArray(items) || (!items.length && !append)) {
    if (!append) list.innerHTML = emptyState('Ничего не найдено');
    return;
  }
  items.forEach((item) => list.appendChild(buildItemCard(item)));
}

/* ---------- Загрузка маркета ---------- */
async function loadMarket(reset = true) {
  const reqId = ++marketReqId;
  const list = document.getElementById('marketList');
  const more = document.getElementById('marketMoreBtn');
  if (reset) {
    marketOffset = 0;
    marketHasMore = false;
    list.innerHTML = '<div class="skeleton tile sk"></div><div class="skeleton tile sk"></div>';
    more.classList.add('hidden');
  } else if (marketLoading) return;
  marketLoading = true;
  const params = new URLSearchParams();
  const q = document.getElementById('searchInput').value.trim();
  const cat = document.getElementById('categoryFilter').value;
  if (q) params.append('q', q);
  if (cat) params.append('category_id', cat);
  addFilterParams(params);
  params.append('sort', document.getElementById('sortSelect').value || 'new');
  params.append('limit', PAGE_SIZE);
  params.append('offset', marketOffset);

  const items = await apiGet('/api/market?' + params.toString());
  if (reqId !== marketReqId) return; // пришёл устаревший ответ
  marketLoading = false;
  if (isError(items)) {
    list.innerHTML = `<p class="hint">${esc(items.detail)}</p>`;
    return;
  }
  renderItemsInto(list, items, !reset);
  marketOffset += items.length;
  marketHasMore = items.length >= PAGE_SIZE;
  // Где работает автоподгрузка, кнопка «Показать ещё» не нужна.
  more.classList.toggle('hidden', !marketHasMore || 'IntersectionObserver' in window);
  if (typeof rearmSentinel === 'function') rearmSentinel();
}

function countView(itemId) {
  fetch(`/api/market/${itemId}/view`, { method: 'POST', headers }).catch(() => {});
}
function countContact(itemId) {
  fetch(`/api/market/${itemId}/contact`, { method: 'POST', headers }).catch(() => {});
}

async function buyItem(shopId, itemId, sel = {}) {
  if (!(await requireRegistered())) return;
  if (!(await askConfirm('Оформить заказ на этот товар? Магазин получит уведомление.'))) return;
  const resp = await apiPost(`/api/shops/${shopId}/order/${itemId}`, { quantity: 1, size: sel.size || null, color: sel.color || null });
  if (!isError(resp) && resp.ok) {
    notify(`Заказ #${resp.order_id} оформлен! Следить за ним можно во вкладке «Покупки».`);
  } else {
    notify(resp?.detail || 'Не удалось оформить заказ');
  }
}

/* ---------- Форма объявления (создание / редактирование) ---------- */
let itemState = { itemId: null, shopId: null, existing: [], removed: new Set(), sending: false };

function _setDeliveryChecks(prefix, methods) {
  ['pickup', 'post'].forEach((m) => {
    const el = document.getElementById(`${prefix}${m}`);
    if (el) el.checked = (methods || '').includes(m);
  });
}

function _readDelivery(prefix) {
  return ['pickup', 'post'].filter((m) => document.getElementById(`${prefix}${m}`)?.checked).join(',');
}

function updateItemContactMode() {
  const has = document.getElementById('itemPhone').value.trim().length > 0;
  document.getElementById('itemContactModeBox').classList.toggle('hidden', !has);
}

function renderExistingPhotos() {
  const box = document.getElementById('itemExistingPhotos');
  box.innerHTML = '';
  itemState.existing.filter((p) => !itemState.removed.has(p.id)).forEach((p) => {
    const wrap = document.createElement('div');
    wrap.className = 'thumb';
    wrap.innerHTML = `<img src="${esc(mediaUrl(p.file_path))}" alt=""><button type="button" title="Убрать">✕</button>`;
    wrap.querySelector('button').onclick = () => { itemState.removed.add(p.id); renderExistingPhotos(); };
    box.appendChild(wrap);
  });
}

/* openItemModal({ item }) — редактирование; { item, duplicate: true } — копия объявления (без фото);
   { shop } — новое объявление магазина; без аргументов — частное. */
async function openItemModal({ item = null, shop = null, duplicate = false } = {}) {
  if (!(await requireRegistered())) return;
  const editing = !!item && !duplicate;
  const isShop = item ? !!item.shop_id : !!shop;
  itemState = {
    itemId: editing ? item.id : null,
    shopId: item ? item.shop_id : (shop ? shop.id : null),
    existing: editing ? [...item.photos] : [], removed: new Set(), sending: false,
  };

  document.getElementById('itemModalTitle').textContent = editing ? 'Редактирование объявления' : (duplicate ? 'Копия объявления' : (isShop ? 'Новое объявление магазина' : 'Новое объявление'));
  document.getElementById('submitItem').textContent = editing ? 'Сохранить' : 'Отправить на модерацию';
  document.getElementById('itemBrand').value = item?.brand || '';
  document.getElementById('itemCondition').value = item?.condition || '';
  document.getElementById('itemSizes').value = item?.sizes || '';
  document.getElementById('itemColors').value = item?.colors || '';
  document.getElementById('itemShopNote').classList.toggle('hidden', !isShop);
  document.getElementById('itemPrivateFields').classList.toggle('hidden', isShop);

  document.getElementById('itemCategory').value = item?.category_id ? String(item.category_id) : '';
  document.getElementById('itemTitle').value = item?.title || '';
  document.getElementById('itemDesc').value = item?.description || '';
  document.getElementById('itemPrice').value = item?.price || '';
  document.getElementById('itemCurrency').value = item?.currency || 'BYN';
  document.getElementById('itemCity').value = item ? (item.city || '') : (me.city || '');
  document.getElementById('itemPhone').value = item ? (item.phone || '') : '';
  document.getElementById('itemContactMode').value = item?.contact_mode || 'messages';
  _setDeliveryChecks('del_', item?.delivery_methods);
  document.getElementById('pickupAddress').value = item?.pickup_address || '';
  document.getElementById('itemPhotos').value = '';
  updateItemContactMode();
  renderExistingPhotos();
  document.getElementById('itemModal').classList.remove('hidden');
}

function closeItemModal() {
  document.getElementById('itemModal').classList.add('hidden');
}

async function submitItemForm() {
  if (itemState.sending) return;
  const title = document.getElementById('itemTitle').value.trim();
  if (title.length < 2) { notify('Введите название (минимум 2 символа)'); return; }

  const files = Array.from(document.getElementById('itemPhotos').files || []);
  const keepCount = itemState.existing.filter((p) => !itemState.removed.has(p.id)).length;
  if (keepCount + files.length > 7) { notify('Можно прикрепить не более 7 фото'); return; }

  const isShop = !!itemState.shopId;
  const fd = new FormData();
  fd.append('title', title);
  fd.append('description', document.getElementById('itemDesc').value.trim());
  fd.append('price', document.getElementById('itemPrice').value.trim());
  fd.append('currency', document.getElementById('itemCurrency').value);
  fd.append('brand', document.getElementById('itemBrand').value.trim());
  fd.append('condition', document.getElementById('itemCondition').value);
  fd.append('sizes', document.getElementById('itemSizes').value.trim());
  fd.append('colors', document.getElementById('itemColors').value.trim());
  const cat = document.getElementById('itemCategory').value;
  if (cat) fd.append('category_id', cat);
  if (!isShop) {
    fd.append('city', document.getElementById('itemCity').value.trim());
    const phone = document.getElementById('itemPhone').value.trim();
    fd.append('phone', phone);
    fd.append('contact_mode', phone ? document.getElementById('itemContactMode').value : 'messages');
    fd.append('delivery_methods', _readDelivery('del_'));
    fd.append('pickup_address', document.getElementById('pickupAddress').value.trim());
  }
  if (!itemState.itemId && isShop) fd.append('shop_id', itemState.shopId);
  if (itemState.itemId) fd.append('remove_photos', Array.from(itemState.removed).join(','));
  const packed = await Promise.all(files.map((f) => compressImage(f)));
  packed.forEach((f) => fd.append('photos', f));

  const btn = document.getElementById('submitItem');
  itemState.sending = true;
  btn.disabled = true;
  const res = itemState.itemId
    ? await apiForm(`/api/market/${itemState.itemId}`, 'PUT', fd)
    : await apiForm('/api/market', 'POST', fd);
  itemState.sending = false;
  btn.disabled = false;

  if (isError(res)) { notify(res.detail); return; }
  closeItemModal();
  const wasEdit = !!itemState.itemId;
  if (res.status === 'pending') {
    notify(wasEdit
      ? 'Изменения отправлены на повторную модерацию. Объявление вернётся в ленту после проверки — бот пришлёт уведомление.'
      : 'Объявление отправлено на модерацию. Оно появится в ленте после проверки — бот пришлёт уведомление.');
  } else {
    notify('Объявление сохранено');
  }
  // Свежее объявление в ленте не видно до одобрения — ведём пользователя в «Мои объявления», где виден статус.
  if (currentSection() !== 'myads' && res.status === 'pending') navigate('myads');
  else if (currentSection() === 'myads') loadMyAds();
  else if (currentSection() === 'market') loadMarket(true);
}

async function deleteItem(item) {
  if (!(await askConfirm(`Удалить объявление «${item.title}»?`))) return;
  const res = await apiDelete(`/api/market/${item.id}`);
  if (isError(res)) { notify(res.detail); return; }
  loadMyAds();
}

/* ---------- События ---------- */
document.getElementById('addItemBtn')?.addEventListener('click', startNewAd);
document.getElementById('cancelItem')?.addEventListener('click', closeItemModal);
document.getElementById('submitItem')?.addEventListener('click', submitItemForm);
document.getElementById('itemPhone')?.addEventListener('input', updateItemContactMode);
document.getElementById('marketMoreBtn')?.addEventListener('click', () => loadMarket(false));
document.getElementById('searchInput')?.addEventListener('input', () => {
  clearTimeout(window._searchTimer);
  window._searchTimer = setTimeout(() => loadMarket(true), 400);
});
document.getElementById('categoryFilter')?.addEventListener('change', () => loadMarket(true));
document.getElementById('sortSelect')?.addEventListener('change', () => loadMarket(true));
/* Единая точка входа «Новое объявление»: владельцу активного магазина предлагаем выбрать — частное или от магазина. */
async function startNewAd() {
  if (!(await requireRegistered())) return;
  if (!me.loaded || typeof checkShopStatus !== 'function') return openItemModal();
  await checkShopStatus();
  if (myShop && myShop.status === 'active') {
    document.getElementById('adChoiceModal').classList.remove('hidden');
  } else {
    openItemModal();
  }
}
const _closeAdChoice = () => document.getElementById('adChoiceModal').classList.add('hidden');
document.getElementById('adChoicePrivate')?.addEventListener('click', () => { _closeAdChoice(); openItemModal(); });
document.getElementById('adChoiceShop')?.addEventListener('click', () => { _closeAdChoice(); openItemModal({ shop: myShop }); });
document.getElementById('adChoiceCancel')?.addEventListener('click', _closeAdChoice);
document.getElementById('adChoiceModal')?.addEventListener('click', (e) => { if (e.target.id === 'adChoiceModal') _closeAdChoice(); });
document.getElementById('navAddBtn')?.addEventListener('click', startNewAd);
document.getElementById('heroBtn')?.addEventListener('click', () => document.getElementById('catalog')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
document.getElementById('catAllBtn')?.addEventListener('click', () => {
  document.getElementById('categoryFilter').value = '';
  renderCategoryTiles();
  loadMarket(true);
});
