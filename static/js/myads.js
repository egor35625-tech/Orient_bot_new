let myAdsMode = 'private';
let shopOrdersLoaded = false;

function updateMyAdsShopChip() {
  const chip = document.getElementById('myAdsShopChip');
  if (!chip) return;
  const has = !!(myShop || managedShop);
  chip.classList.toggle('hidden', !has);
  if (!has && myAdsMode === 'shop') setMyAdsMode('private');
}

function setMyAdsMode(mode) {
  myAdsMode = mode;
  document.querySelectorAll('[data-myads]').forEach((c) => c.classList.toggle('active', c.dataset.myads === mode));
  document.getElementById('myAdsPrivate').classList.toggle('hidden', mode !== 'private');
  document.getElementById('myAdsShop').classList.toggle('hidden', mode !== 'shop');
}

function myAdsGate(show) {
  // show=true — пользователь не зарегистрирован: показываем приглашение вместо списков
  let gate = document.getElementById('myAdsGate');
  if (!gate) {
    gate = document.createElement('div');
    gate.id = 'myAdsGate';
    gate.className = 'card';
    gate.innerHTML = `<h3>Нужна регистрация</h3>
      <p class="hint left">Чтобы создавать объявления, укажите ваш @username в Telegram.</p>
      <button class="primary mt">Зарегистрироваться</button>`;
    gate.querySelector('button').onclick = async () => { if (await requireRegistered()) loadMyAds(); };
    document.getElementById('tab-myads').prepend(gate);
  }
  gate.classList.toggle('hidden', !show);
  document.getElementById('myAdsChips').classList.toggle('hidden', show);
  document.getElementById('myAdsPrivate').classList.toggle('hidden', show || myAdsMode !== 'private');
  document.getElementById('myAdsShop').classList.toggle('hidden', show || myAdsMode !== 'shop');
}

/* Активные объявления — карточками, проданные — в сворачиваемом архиве. */
function renderMine(listEl, items, emptyText) {
  listEl.classList.add('market-grid');
  listEl.innerHTML = '';
  const active = items.filter((i) => !i.sold);
  const sold = items.filter((i) => i.sold);
  if (!active.length) listEl.innerHTML = `<p class="hint">${emptyText}</p>`;
  active.forEach((it) => listEl.appendChild(buildItemCard(it, { mine: true })));
  if (sold.length) {
    const det = document.createElement('details');
    det.className = 'archive';
    det.style.gridColumn = '1 / -1';
    det.innerHTML = `<summary>Проданные (${sold.length})</summary><div class="market-grid"></div>`;
    sold.forEach((it) => det.querySelector('.market-grid').appendChild(buildItemCard(it, { mine: true })));
    listEl.appendChild(det);
  }
}

async function loadMyAds() {
  if (!me.loaded) await refreshMe();
  if (!me.is_registered) { myAdsGate(true); return; }
  myAdsGate(false);
  await checkShopStatus();

  const [res] = await Promise.all([apiGet('/api/market/my')]);
  const priv = document.getElementById('myPrivateList');
  const shopList = document.getElementById('myShopList');
  if (isError(res)) {
    priv.innerHTML = `<p class="hint">${esc(res.detail)}</p>`;
    return;
  }
  renderMine(priv, res.private, 'У вас пока нет объявлений');

  renderMyShopPanel(res.shop || []);
  setMyAdsMode(myAdsMode === 'shop' && (myShop || managedShop) ? 'shop' : 'private');
  void shopList;
}

function renderMyShopPanel(items) {
  const card = document.getElementById('myShopCard');
  const addBtn = document.getElementById('myAddShopBtn');
  const list = document.getElementById('myShopList');
  const orders = document.getElementById('shopOrdersBox');
  list.innerHTML = '';

  if (!myShop && managedShop) {
    card.innerHTML = `<div class="card"><p class="note">Вы менеджер магазина «${esc(managedShop.name)}». Объявления добавляет владелец, а вы можете вести заказы.</p></div>`;
    addBtn.classList.add('hidden');
  } else if (myShop) {
    card.innerHTML = `<div class="card">${myShopCardHtml(myShop, myApplication)}
      ${myApplication && myApplication.status === 'pending' ? '' : '<button class="secondary small mt" id="myShopEditBtn">✏️ Изменить магазин</button>'}</div>`;
    const edit = document.getElementById('myShopEditBtn');
    if (edit) edit.onclick = openShopForm;
    addBtn.classList.toggle('hidden', myShop.status !== 'active');
    renderMine(list, items, 'В магазине пока нет объявлений');
  } else {
    card.innerHTML = '';
    addBtn.classList.add('hidden');
  }
  orders.classList.toggle('hidden', !(myShop || managedShop));
  shopOrdersLoaded = false;
  if (orders.open) { shopOrdersLoaded = true; loadShopOrders(); }
}

document.querySelectorAll('[data-myads]').forEach((chip) => {
  chip.addEventListener('click', () => setMyAdsMode(chip.dataset.myads));
});
document.getElementById('myAddPrivateBtn')?.addEventListener('click', () => openItemModal());
document.getElementById('myAddShopBtn')?.addEventListener('click', () => { if (myShop) openItemModal({ shop: myShop }); });
document.getElementById('shopOrdersBox')?.addEventListener('toggle', (e) => {
  if (e.target.open && !shopOrdersLoaded) { shopOrdersLoaded = true; loadShopOrders(); }
});
