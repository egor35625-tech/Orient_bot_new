/* Избранное хранится на устройстве (localStorage): id -> снимок объявления. */
const FAV_KEY = 'orient_favs';
let favs = {};
try { favs = JSON.parse(localStorage.getItem(FAV_KEY) || '{}') || {}; } catch (e) { favs = {}; }

function saveFavs() {
  try { localStorage.setItem(FAV_KEY, JSON.stringify(favs)); } catch (e) { /* ignore */ }
}
function isFav(id) { return Object.prototype.hasOwnProperty.call(favs, id); }
function favCount() { return Object.keys(favs).length; }

function toggleFav(item) {
  const on = !isFav(item.id);
  if (on) favs[item.id] = item; else delete favs[item.id];
  if (!item.is_mine) apiPost(`/api/market/${item.id}/fav`, { on }); // счётчик «в избранном» для продавца
  saveFavs();
  try { tg?.HapticFeedback?.impactOccurred('light'); } catch (e) { /* ignore */ }
  refreshFavUi();
  if (currentSection() === 'favs') renderFavs();
}

function refreshFavUi() {
  document.querySelectorAll('[data-fav-id]').forEach((el) => {
    el.classList.toggle('on', isFav(el.dataset.favId));
  });
  const n = favCount();
  const c = document.getElementById('drawerFavCount');
  if (c) { c.textContent = n; c.classList.toggle('hidden', !n); }
  document.getElementById('navFavDot')?.classList.toggle('hidden', !n);
}

function renderFavs() {
  const list = document.getElementById('favList');
  if (!list) return;
  const items = Object.values(favs).reverse();
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = emptyState('Здесь появятся объявления, которые вы отметите сердечком');
    return;
  }
  items.forEach((it) => list.appendChild(buildItemCard(it)));
}
