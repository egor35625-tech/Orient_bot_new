/* Корзина: хранится на устройстве, товары только одного магазина.
   При оформлении каждый товар становится отдельным заказом (свой этап и чат). */
const CART_KEY = 'orient_cart';
let cart = { shopId: null, shopName: '', lines: [] };
try {
  const saved = JSON.parse(localStorage.getItem(CART_KEY) || 'null');
  if (saved && Array.isArray(saved.lines)) cart = saved;
} catch (e) { /* ignore */ }

function saveCart() {
  try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (e) { /* ignore */ }
}
function cartCount() { return cart.lines.reduce((n, l) => n + l.qty, 0); }
const lineKey = (itemId, size, color) => `${itemId}|${size || ''}|${color || ''}`;

function parsePrice(v) {
  const n = parseFloat(String(v ?? '').replace(/\s| /g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

function refreshCartUi() {
  const n = cartCount();
  const badge = document.getElementById('cartCount');
  if (badge) { badge.textContent = n; badge.classList.toggle('hidden', !n); }
  const d = document.getElementById('drawerCartCount');
  if (d) { d.textContent = n; d.classList.toggle('hidden', !n); }
}

async function cartAdd(item, sel = {}) {
  if (cart.lines.length && cart.shopId !== item.shop_id) {
    const ok = await askConfirm(`В корзине товары магазина «${cart.shopName}». Очистить её и добавить этот товар?`);
    if (!ok) return false;
    cart = { shopId: null, shopName: '', lines: [] };
  }
  cart.shopId = item.shop_id;
  cart.shopName = item.shop?.name || cart.shopName || 'Магазин';
  const key = lineKey(item.id, sel.size, sel.color);
  const line = cart.lines.find((l) => l.key === key);
  if (line) line.qty = Math.min(20, line.qty + 1);
  else {
    cart.lines.push({
      key, itemId: item.id, title: item.title, price: item.price || '', currency: item.currency || 'BYN',
      photo: item.photos && item.photos[0] ? item.photos[0].file_path : null,
      size: sel.size || null, color: sel.color || null, qty: 1,
    });
  }
  saveCart();
  refreshCartUi();
  try { tg?.HapticFeedback?.notificationOccurred('success'); } catch (e) { /* ignore */ }
  notify('Добавлено в корзину');
  return true;
}

function cartTotalText() {
  const sums = {};
  let unknown = false;
  cart.lines.forEach((l) => {
    const p = parsePrice(l.price);
    if (p === null) { unknown = true; return; }
    sums[l.currency] = (sums[l.currency] || 0) + p * l.qty;
  });
  const parts = Object.entries(sums).map(([cur, v]) => `${fmtPrice(String(Math.round(v * 100) / 100))} ${cur}`);
  if (!parts.length) return 'по договорённости';
  return parts.join(' + ') + (unknown ? ' + договорные' : '');
}

function renderCart() {
  const list = document.getElementById('cartList');
  const footer = document.getElementById('cartFooter');
  document.getElementById('cartShop').textContent = cart.lines.length ? `Магазин: ${cart.shopName}` : '';
  document.getElementById('cartClear').classList.toggle('hidden', !cart.lines.length);
  list.innerHTML = '';
  if (!cart.lines.length) {
    list.innerHTML = emptyState('В корзине пока пусто. Добавляйте товары магазинов — они появятся здесь.');
    footer.classList.add('hidden');
    return;
  }
  footer.classList.remove('hidden');
  cart.lines.forEach((l) => {
    const opts = [l.size ? `Размер ${l.size}` : '', l.color ? l.color : ''].filter(Boolean).join(' · ');
    const price = l.price ? `${fmtPrice(l.price)} ${l.currency}` : 'Договорная';
    const row = document.createElement('div');
    row.className = 'cart-line';
    row.innerHTML = `
      <div class="cart-img">${l.photo ? `<img src="${esc(mediaUrl(l.photo))}" alt="">` : NO_PHOTO_HTML}</div>
      <div class="cart-info">
        <div class="cart-title">${esc(l.title)}</div>
        ${opts ? `<div class="cart-opts">${esc(opts)}</div>` : ''}
        <div class="cart-price">${esc(price)}</div>
        <div class="qty"><button data-d="-1" aria-label="Меньше">−</button><span>${l.qty}</span><button data-d="1" aria-label="Больше">+</button></div>
        <button class="cart-x" aria-label="Удалить"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>`;
    row.querySelector('.cart-img').onclick = () => openItemById(l.itemId);
    row.querySelectorAll('.qty button').forEach((b) => {
      b.onclick = () => {
        l.qty = Math.max(1, Math.min(20, l.qty + Number(b.dataset.d)));
        saveCart(); refreshCartUi(); renderCart();
      };
    });
    row.querySelector('.cart-x').onclick = () => {
      cart.lines = cart.lines.filter((x) => x.key !== l.key);
      if (!cart.lines.length) cart = { shopId: null, shopName: '', lines: [] };
      saveCart(); refreshCartUi(); renderCart();
    };
    list.appendChild(row);
  });
  document.getElementById('cartTotal').textContent = cartTotalText();
}

let checkingOut = false;
async function cartCheckout() {
  if (checkingOut || !cart.lines.length) return;
  if (!(await requireRegistered())) return;
  if (!(await askConfirm(`Оформить ${cart.lines.length} ${cart.lines.length === 1 ? 'заказ' : 'заказа(ов)'} в магазине «${cart.shopName}»?`))) return;
  checkingOut = true;
  const btn = document.getElementById('cartCheckout');
  btn.disabled = true;
  const failed = [];
  let okCount = 0;
  for (const l of [...cart.lines]) {
    const res = await apiPost(`/api/shops/${cart.shopId}/order/${l.itemId}`, { quantity: l.qty, size: l.size, color: l.color });
    if (!isError(res) && res.ok) {
      okCount += 1;
      cart.lines = cart.lines.filter((x) => x.key !== l.key);
    } else {
      failed.push(`«${l.title}»: ${res?.detail || 'ошибка'}`);
    }
  }
  if (!cart.lines.length) cart = { shopId: null, shopName: '', lines: [] };
  saveCart(); refreshCartUi(); renderCart();
  checkingOut = false;
  btn.disabled = false;
  if (failed.length) notify(`Оформлено: ${okCount}. Не удалось:\n${failed.join('\n')}`);
  else notify(`Готово! Оформлено заказов: ${okCount}. Следите за ними во вкладке «Покупки».`);
  if (okCount && !failed.length) navigate('orders');
}

document.getElementById('cartBtn')?.addEventListener('click', () => navigate('cart'));
document.getElementById('cartCheckout')?.addEventListener('click', cartCheckout);
document.getElementById('cartClear')?.addEventListener('click', async () => {
  if (!(await askConfirm('Очистить корзину?'))) return;
  cart = { shopId: null, shopName: '', lines: [] };
  saveCart(); refreshCartUi(); renderCart();
});
