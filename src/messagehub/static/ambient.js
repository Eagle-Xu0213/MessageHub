(() => {
  const KEY = 'messagehub.theme';
  const frames = [
    [0, [180,187,211], [202,195,214], 45],
    [6, [213,213,220], [232,205,195], 15],
    [10,[195,212,229], [199,221,211], 38],
    [14,[190,210,229], [198,220,211], 50],
    [17,[210,207,224], [231,203,196], 68],
    [20,[190,193,217], [213,197,215], 80],
    [24,[180,187,211], [202,195,214], 45]
  ];
  let settings = {theme:'auto'};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch {}
  if (!['auto','light','dark'].includes(settings.theme)) settings.theme = 'auto';

  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function resolved() {
    if (settings.theme !== 'auto') return settings.theme;
    return mq && mq.matches ? 'dark' : 'light';
  }
  const forced = new URLSearchParams(location.search).get('theme');
  function apply() {
    const root = document.documentElement;
    if (root.dataset) root.dataset.theme = ['light','dark'].includes(forced) ? forced : resolved();
  }
  function save(changes) {
    settings = {...settings, ...changes};
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch {}
    apply();
  }
  function calculate(hour) {
    hour = ((hour % 24) + 24) % 24;
    const index = frames.findIndex(frame => frame[0] > hour);
    const [a,b] = frames.slice(index - 1, index + 1);
    const fraction = (hour - a[0]) / (b[0] - a[0]);
    const t = fraction * fraction * (3 - 2 * fraction);
    const mix = (x,y) => x + (y - x) * t;
    const color = (x,y) => x.map((value,i) => Math.round(mix(value,y[i])));
    return {first:color(a[1],b[1]), second:color(a[2],b[2]), x:mix(a[3],b[3])};
  }

  window.MessageHubAmbient = {calculate, apply, save, getSettings:() => ({...settings})};
  apply();
  if (mq && mq.addEventListener) mq.addEventListener('change', () => { if (settings.theme === 'auto') apply(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) apply(); });
})();
