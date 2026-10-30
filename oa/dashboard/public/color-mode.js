// Apply the saved appearance before styles and React load to avoid a light flash.
(() => {
  let preference
  try { preference = localStorage.getItem('sanity-color-mode') } catch { /* Storage is optional. */ }
  const dark = preference === 'dark' || preference !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches
  const root = document.documentElement
  root.dataset.theme = dark ? 'dark' : 'light'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#141323' : '#f8f7ff')
})()
