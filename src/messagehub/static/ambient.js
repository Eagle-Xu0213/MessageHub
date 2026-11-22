(() => {
  const frames = [
    [0, [180,187,211], [202,195,214], 45],
    [6, [213,213,220], [232,205,195], 15],
    [10,[195,212,229], [199,221,211], 38],
    [14,[190,210,229], [198,220,211], 50],
    [17,[210,207,224], [231,203,196], 68],
    [20,[190,193,217], [213,197,215], 80],
    [24,[180,187,211], [202,195,214], 45]
  ];
  let settings = {mode:'auto', strength:65, dynamic:true, scene:12};
  try {
    Object.assign(settings, JSON.parse(localStorage.getItem('messagehub.ambient')));
  } catch {}
  if (![7,12,19,23].includes(settings.scene)) settings.scene = 12;

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

  const style = document.documentElement.style;
  let base, next, overlay = false;
  function apply(immediate = false) {
    const now = new Date();
    let hour = now.getHours() + now.getMinutes()/60 + now.getSeconds()/3600;
    const preview = new URLSearchParams(location.search).get('ambient_preview');
    if (location.hostname === 'localhost' && preview !== null && Number.isFinite(Number(preview))) {
      hour = Number(preview);
    }
    if (settings.mode === 'fixed') hour = settings.scene;
    else if (!settings.dynamic) hour = 12;
    const light = calculate(hour);
    const strength = Math.max(0,Math.min(100,Number(settings.strength)||0))/100;
    const x = light.x.toFixed(2);
    const first = 'rgba(' + light.first.join(',') + ',' + .85*strength + ')';
    const second = 'rgba(' + light.second.join(',') + ',' + .78*strength + ')';
    const background = 'radial-gradient(ellipse at ' + x + '% 0%,' + first +
      ',transparent 72%),radial-gradient(ellipse at 90% 95%,' + second +
      ',transparent 65%),linear-gradient(145deg,#ececf2,#e0e7eb 60%,#ece6ec)';
    if (!base || immediate) {
      base = next = background;
      style.setProperty('--ambient-base',base);
      style.setProperty('--ambient-next',next);
    } else if (background !== (overlay ? next : base)) {
      if (overlay) {
        base = background;
        style.setProperty('--ambient-base',base);
      } else {
        next = background;
        style.setProperty('--ambient-next',next);
      }
      overlay = !overlay;
      style.setProperty('--ambient-fade',overlay ? '1' : '0');
    }
  }

  function save(changes) {
    settings = {...settings, ...changes};
    try { localStorage.setItem('messagehub.ambient',JSON.stringify(settings)); } catch {}
    apply(true);
  }

  let timer;
  function resume() {
    clearInterval(timer);
    if (document.hidden) return;
    apply();
    timer = setInterval(() => {
      if (settings.dynamic && settings.mode === 'auto') apply();
    },60000);
  }

  window.MessageHubAmbient = {calculate, apply, save, getSettings:() => ({...settings})};
  apply(true);
  document.addEventListener('visibilitychange',resume);
  window.addEventListener('pageshow',resume);
})();
