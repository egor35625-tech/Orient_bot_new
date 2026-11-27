(async function boot() {
  loadSavedTheme();
  refreshFavUi();
  refreshCartUi();
  renderRecent();
  updateFilterUi();
  loadConfig();
  loadCategories();
  loadMarket(true);
  await refreshMe();
  updateDrawerProfile();
  checkShopStatus();
  syncFavs();

  // Короткая заставка с логотипом (минимум 900 мс, чтобы не мелькала).
  await new Promise((r) => setTimeout(r, 900));
  hideSplash();

  const afterTour = () => {
    // Незарегистрированному пользователю предлагаем указать @username (можно отложить и просто смотреть маркет).
    if (me.loaded && !me.is_registered) showRegModal();
    handleDeepLink();
  };
  if (!LS.get('orient_onboarded', false)) showOnboarding(afterTour);
  else afterTour();
})();
