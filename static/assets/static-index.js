StaticAuth.init({ pageName: '统一页面入口' });

document.querySelectorAll('[data-protected-link]').forEach((link) => {
  link.addEventListener('click', async (event) => {
    event.preventDefault();
    const target = link.getAttribute('href');
    if (!target) {
      return;
    }

    try {
      await StaticAuth.requireAuth({ message: '请先登录后进入控制台页面' });
      window.location.href = target;
    } catch (error) {
      if (!StaticAuth.isAuthError(error)) {
        console.error(error);
      }
    }
  });
});
