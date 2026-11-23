(() => {
  const q = selector => document.querySelector(selector);
  const pages = ['home', 'tasks', 'messages', 'system', 'settings'];
  const menu = q('.nav-toggle');
  const captureUrl = '/api/dashboard/capture';
  const identityUrl = '/api/settings/identity';
  const day = 86400000;
  let page = 'home';
  let identityLoaded = false;
  let homeRequest = 0;

  const captureCopy = {
    unselected: ['尚未选择会话', '选择想关注的群或联系人，保存后等待微信同步。', '/select?setup=1', '选择关注会话'],
    waiting: ['等待新快照同步', '保持微信登录。最近保存的范围将在新快照处理完成后生效。', '/live', '查看采集进度'],
    normal: ['同步正常'],
    delayed: ['同步延迟', '超过 15 分钟没有完成新一轮同步。', '#system', '检查运行状态'],
    failed: ['同步失败', '请检查微信登录和采集器状态。', '/live', '查看采集状态'],
    unavailable: ['状态暂不可用', '暂时无法读取采集状态。', '#system', '检查运行状态'],
  };

  function closeMenu() {
    menu.setAttribute('aria-expanded', 'false');
  }

  menu.onclick = () => menu.setAttribute('aria-expanded', String(menu.getAttribute('aria-expanded') !== 'true'));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') {
      closeMenu();
      menu.focus();
    }
  });

  async function get(url) {
    const response = await fetch(url, {signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('请求失败');
    return response.json();
  }

  function show(name, taskView) {
    closeMenu();
    page = pages.includes(name) ? name : 'home';
    for (const section of pages) q('#' + section + '-page').hidden = section !== page;
    for (const button of document.querySelectorAll('.side-nav [data-page]')) {
      const current = button.dataset.page === page;
      button.classList.toggle('active', current);
      if (current) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    if (page === 'home') home();
    if (page === 'system') system();
    if (page === 'tasks' && taskView) {
      view = taskView;
      expanded = null;
      render();
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-page]');
    if (!button) return;
    show(button.dataset.page, button.dataset.taskView);
    history.replaceState(null, '', '#' + button.dataset.page);
  });

  q('#open-import').onclick = () => {
    show('tasks');
    history.replaceState(null, '', '#tasks');
    const controls = q('#import-controls');
    controls.closest('details').open = true;
    q('#file').focus();
    controls.scrollIntoView({block: 'center'});
  };

  q('#new-item-inline').onclick = () => q('#new-item').click();
  const hour = new Date().getHours();
  q('#greeting').textContent = hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好';

  function captureLabel(data) {
    return (captureCopy[data?.phase] || captureCopy.unavailable)[0];
  }

  function syncTime(data) {
    return data?.last_sync ? '最近成功同步 ' + data.last_sync.replace('T', ' ').slice(0, 16) : '暂无成功同步记录';
  }

  async function capture() {
    return get(captureUrl).catch(() => null);
  }

  function showSetup(data) {
    const form = q('#identity-form');
    const link = q('#setup-link');
    const needsIdentity = data?.identity_configured === false;
    const [title, description, href, action] = captureCopy[data?.phase] || captureCopy.unavailable;
    q('#setup-guide').hidden = !needsIdentity && data?.phase === 'normal';
    form.hidden = !needsIdentity;
    link.hidden = needsIdentity || !href;
    q('#setup-title').textContent = needsIdentity ? '确认自己的微信名字' : title;
    if (!needsIdentity || !q('#setup-description').dataset.error) {
      q('#setup-description').textContent = needsIdentity
        ? '填写微信昵称或群内发言名，帮助 MessageHub 识别与你有关的消息。'
        : description || '';
    }
    if (href) {
      link.href = href;
      link.textContent = action;
    }
    if (needsIdentity && !identityLoaded) {
      identityLoaded = true;
      get(identityUrl).then(names => {
        for (const key of ['nicknames', 'sender_names']) {
          if (!form.elements[key].value) form.elements[key].value = names[key].join('，');
        }
      }).catch(() => { identityLoaded = false; });
    }
  }

  q('#identity-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button');
    const message = q('#setup-description');
    const data = Object.fromEntries(['nicknames', 'sender_names'].map(key => [
      key, form.elements[key].value.split(/[,，]/).map(value => value.trim()).filter(Boolean),
    ]));
    submit.disabled = true;
    try {
      const response = await fetch(identityUrl, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error((await response.json()).detail || '保存失败');
      delete message.dataset.error;
      await home();
    } catch (error) {
      message.dataset.error = 'true';
      message.textContent = error.message || '保存失败，请重试。';
    } finally {
      submit.disabled = false;
    }
  };

  function deadline(item) {
    const time = Date.parse(item.deadline);
    return Number.isFinite(time) ? time : Infinity;
  }

  function renderHomeTasks(data, phase) {
    const now = Date.now();
    const list = data.items.filter(active).sort((a, b) => deadline(a) - deadline(b) || b.id - a.id);
    const isSoon = item => deadline(item) >= now && deadline(item) <= now + day;
    q('#pending-count').textContent = list.length;
    q('#soon-count').textContent = list.filter(isSoon).length + ' 项即将到期';
    q('#review-count').textContent = data.items.filter(item => item.status === 'review').length;
    if (!list.length) {
      q('#home-tasks').textContent = phase === 'normal' ? '同步正常，暂无待处理事项' : '暂无待处理事项';
      return;
    }
    q('#home-tasks').innerHTML = list.slice(0, 5).map(item => {
      const due = deadline(item) < now ? '已逾期 · ' : isSoon(item) ? '即将到期 · ' : '';
      const time = item.deadline ? '截止 ' + item.deadline.replace('T', ' ').slice(0, 16) : '无截止时间';
      return '<div class="home-task"><button class="check" data-complete="' + item.id +
        '" aria-label="完成事项"></button><button class="task-open" data-task="' + item.id + '">' +
        esc(title(item)) + '<small>' + esc(due + time + ' · ' + (item.conversation_name || '手动创建')) +
        '</small></button><span aria-hidden="true">›</span></div>';
    }).join('');
  }

  async function home() {
    const request = ++homeRequest;
    const [state, result] = await Promise.all([capture(), get('/api/workbench/items').catch(() => null)]);
    if (request !== homeRequest) return;
    q('#capture-title').textContent = captureLabel(state);
    q('#capture-time').textContent = syncTime(state);
    q('#capture-bar').textContent = captureLabel(state) + ' · ' + syncTime(state) + ' ›';
    showSetup(state);
    if (result) renderHomeTasks(result, state?.phase);
    else {
      q('#home-tasks').textContent = '待办加载失败，请刷新重试。';
      q('#pending-count').textContent = '—';
      q('#review-count').textContent = '—';
      q('#soon-count').textContent = '暂不可用';
    }
  }

  document.addEventListener('click', async event => {
    const done = event.target.closest('[data-complete]');
    const task = event.target.closest('[data-task]');
    if (!done && !task) return;
    const id = Number(done?.dataset.complete || task.dataset.task);
    if (done) done.disabled = true;
    try {
      await load();
      const item = items.find(entry => entry.id === id);
      if (!item || !active(item)) throw new Error('事项已更新，请查看最新列表。');
      if (done) {
        await changeStatus(item, 'completed');
        q('#home-feedback').textContent = '';
        await home();
      } else {
        show('tasks', 'pending');
        history.replaceState(null, '', '#task-' + id);
        q('[data-expand="' + id + '"]')?.click();
        q('#task-' + id)?.scrollIntoView({block: 'center'});
      }
    } catch (error) {
      if (done) done.disabled = false;
      await home();
      q('#home-feedback').textContent = error.message || '操作失败，请重试。';
    }
  });

  document.addEventListener('workbench-refresh', () => {
    if (page === 'home') home();
  });

  async function system() {
    const [health, scheduler, state] = await Promise.all([
      get('/api/health').catch(() => null),
      get('/api/personal-tasks/status').catch(() => null),
      capture(),
    ]);
    q('#system-info').innerHTML = [
      '服务：' + (health ? '正常' : '不可用'),
      '采集：' + captureLabel(state),
      syncTime(state),
      '消息处理：' + (scheduler?.capture || '状态不可用'),
      '最近错误：' + (scheduler?.last_error || '暂无'),
    ].map(line => '<p>' + esc(line) + '</p>').join('');
  }

  q('#search-form').onsubmit = async event => {
    event.preventDefault();
    const results = q('#search-results');
    results.textContent = '正在搜索…';
    try {
      const data = await get('/api/messages/search?q=' + encodeURIComponent(q('#search-query').value));
      results.innerHTML = data.messages.length ? data.messages.map(item =>
        '<div class="search-result"><strong>' + esc(item.conversation_name || '未知会话') +
        '</strong> <span class="muted">' + esc(item.sender_name) + ' · ' +
        esc((item.sent_at || '').replace('T', ' ').slice(0, 16)) + '</span><div>' +
        esc(item.text) + '</div></div>').join('') : '没有找到匹配的消息。';
    } catch {
      results.textContent = '搜索失败，请稍后重试。';
    }
  };

  const ambient = window.MessageHubAmbient;
  q('#theme-mode').value = ambient.getSettings().theme;
  q('#theme-mode').onchange = event => ambient.save({theme: event.target.value});
  function showHash() {
    const hash = location.hash.slice(1);
    show(hash.startsWith('task-') ? 'tasks' : hash);
  }
  showHash();
  window.addEventListener('hashchange', showHash);
  setInterval(() => {
    if (page === 'home' && !document.hidden && !q('#identity-form').contains(document.activeElement)) home();
  }, 15000);
})();
