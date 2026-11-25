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
    unselected: ['尚未选择会话', '请在检测范围选择要关注的群聊或联系人。', '/select?setup=1', '选择关注会话'],
    waiting: ['等待下一轮微信同步', '请保持微信登录，刚保存的关注范围将在下一轮同步后生效。', '/live', '查看运行状态'],
    normal: ['同步正常'],
    delayed: ['同步延迟', '已超过 15 分钟未完成同步，请确认微信已登录。', '#system', '查看运行状态'],
    failed: ['同步失败', '请确认微信已登录，并检查同步服务状态。', '/live', '查看运行状态'],
    unavailable: ['无法读取同步状态', '请稍后重试，或查看运行状态。', '#system', '查看运行状态'],
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
    if (!response.ok) throw new Error('操作失败，请稍后重试。');
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
    if (page === 'settings') showCategories();
    if (page === 'messages') loadChats();
    if (page === 'tasks' && taskView) {
      taskBoard = false;
      view = taskView;
      expanded = null;
      render();
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-page]');
    if (!button) return;
    if (button.matches('a')) event.preventDefault();
    show(button.dataset.page, button.dataset.taskView);
    const destination = '#' + button.dataset.page;
    if (location.hash !== destination) history.pushState(null, '', destination);
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
      message.textContent = error.message || '保存失败，请稍后重试。';
    } finally {
      submit.disabled = false;
    }
  };

  function deadline(item) {
    const time = Date.parse(item.deadline);
    return Number.isFinite(time) ? time : Infinity;
  }

  const today = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  let selectedDay = today;
  let calendarItems = null;
  const dayKey = (year, month, date) => [year, String(month).padStart(2, '0'), String(date).padStart(2, '0')].join('-');
  const deadlineDay = item => item.deadline?.slice(0, 10);

  function renderCalendar() {
    const [year, month] = selectedDay.split('-').map(Number);
    const first = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    const count = new Date(year, month, 0).getDate();
    const dueByDay = new Map();
    for (const item of calendarItems || []) {
      const date = deadlineDay(item);
      if (date) dueByDay.set(date, (dueByDay.get(date) || 0) + 1);
    }
    q('#calendar-month').textContent = year + '年' + month + '月';
    const grid = q('#calendar-days');
    const refocus = grid.contains(document.activeElement);
    const weekdays = ['一', '二', '三', '四', '五', '六', '日'].map(d => '<span aria-hidden="true">' + d + '</span>');
    const days = Array.from({length: count}, (_, index) => {
      const date = index + 1;
      const key = dayKey(year, month, date);
      const due = dueByDay.get(key);
      return '<button type="button" data-calendar-day="' + key + '" aria-label="' + month + '月' + date +
        '日' + (due ? '，' + due + '项待办到期' : '') + '" aria-pressed="' + (key === selectedDay) +
        '" class="' + (due ? 'has-tasks' : '') + '">' + date + '</button>';
    });
    grid.innerHTML = weekdays.concat(Array(first).fill('<span aria-hidden="true"></span>'), days).join('');
    if (refocus) grid.querySelector('[aria-pressed="true"]')?.focus({preventScroll: true});
    const selected = (calendarItems || []).filter(item => deadlineDay(item) === selectedDay);
    q('#calendar-results').innerHTML = selected.length
      ? selected.map(item => '<button type="button" class="link" data-task="' + item.id + '">' + esc(title(item)) + '</button>').join('<br>')
      : '<p>' + (calendarItems === null ? '正在读取待办截止日期…' : '这一天没有待办到期') + '</p>';
  }

  function shiftMonth(offset) {
    const [year, month, date] = selectedDay.split('-').map(Number);
    const next = new Date(year, month - 1 + offset, 1);
    const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
    selectedDay = dayKey(next.getFullYear(), next.getMonth() + 1, Math.min(date, last));
    renderCalendar();
  }

  q('#calendar-prev').onclick = () => shiftMonth(-1);
  q('#calendar-next').onclick = () => shiftMonth(1);
  q('#calendar-days').onclick = event => {
    const button = event.target.closest('[data-calendar-day]');
    if (!button) return;
    selectedDay = button.dataset.calendarDay;
    renderCalendar();
  };
  renderCalendar();

  function renderHomeTasks(data, phase) {
    const now = Date.now();
    const list = data.items.filter(active).sort((a, b) => deadline(a) - deadline(b) || b.id - a.id);
    calendarItems = list;
    renderCalendar();
    const isSoon = item => deadline(item) >= now && deadline(item) <= now + day;
    q('#pending-count').textContent = list.length;
    q('#soon-count').textContent = list.filter(isSoon).length + ' 项即将截止';
    q('#review-count').textContent = data.items.filter(item => item.status === 'review').length;
    if (!list.length) {
      q('#home-tasks').textContent = phase === 'normal' ? '同步正常，暂无待处理事项' : '暂无待处理事项';
      return;
    }
    q('#home-tasks').innerHTML = list.slice(0, 5).map(item => {
      const due = deadline(item) < now ? '已逾期 · ' : isSoon(item) ? '即将截止 · ' : '';
      const time = item.deadline ? '截止 ' + item.deadline.replace('T', ' ').slice(0, 16) : '无截止时间';
      return '<div class="home-task"><button class="check" data-complete="' + item.id +
        '" aria-label="完成事项"></button><button class="task-open" data-task="' + item.id + '">' +
        esc(title(item)) + '<small>' + esc(due + time + ' · ' + ((item.source_platform === 'wechat' ? '微信 · ' : '') + (item.conversation_name || '手动创建'))) +
        '</small></button><span aria-hidden="true">›</span></div>';
    }).join('');
  }

  async function home() {
    const request = ++homeRequest;
    const [state, result] = await Promise.all([capture(), get('/api/workbench/items').catch(() => null)]);
    if (request !== homeRequest) return;
    q('.bento-capture').dataset.phase = state?.phase || 'unavailable';
    q('#capture-title').textContent = captureLabel(state);
    q('#capture-time').textContent = syncTime(state);
    q('#capture-bar').textContent = '查看运行状态 →';
    showSetup(state);
    if (result) renderHomeTasks(result, state?.phase);
    else {
      q('#calendar-results').textContent = '截止日期加载失败，请刷新页面重试。';
      q('#home-tasks').textContent = '待办加载失败，请刷新页面重试。';
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
      if (!item || !active(item)) throw new Error('该事项已有变动，请查看最新列表后重试。');
      if (done) {
        await changeStatus(item, 'completed');
        q('#home-feedback').textContent = '';
        await home();
      } else {
        show('tasks', 'pending');
        history.pushState(null, '', '#task-' + id);
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

  const chatSelect = q('#chat-conversation');
  const chatList = q('#chat-message-list');
  const olderButton = q('#chat-older');
  const generateButton = q('#chat-generate');
  const chatStatus = q('#chat-status');
  const generateStatus = q('#chat-generate-status');
  const aiSettings = q('#chat-ai-settings');
  const maxSelected = 20;
  const selected = new Set();
  let conversations = new Map();
  let loadingChats = false;
  let chatRequest = 0;
  let beforeId = null;
  let generating = false;

  async function loadChats() {
    if (loadingChats) return;
    loadingChats = true;
    if (!conversations.size) chatStatus.textContent = '正在读取聊天列表…';
    try {
      const data = await get('/api/messages/conversations');
      const current = chatSelect.value;
      const nameCounts = new Map();
      conversations = new Map();
      for (const chat of data.conversations) {
        const key = JSON.stringify([chat.source_platform, chat.conversation_id]);
        const name = chat.conversation_name || chat.conversation_id;
        const platform = {wechat: '微信', manual: '导入'}[chat.source_platform] || chat.source_platform;
        const label = name + ' · ' + platform;
        conversations.set(key, {...chat, label});
        nameCounts.set(label, (nameCounts.get(label) || 0) + 1);
      }
      chatSelect.replaceChildren(new Option(conversations.size ? '请选择聊天' : '暂无已保存的聊天', ''));
      for (const [key, chat] of conversations) {
        const label = nameCounts.get(chat.label) > 1 ? chat.label + ' · ' + chat.conversation_id : chat.label;
        chatSelect.add(new Option(label, key));
      }
      if (conversations.has(current)) chatSelect.value = current;
      else if (current) openChat();
      if (!current) chatStatus.textContent = conversations.size ? '' : '导入消息或完成微信同步后，这里会显示聊天。';
    } catch {
      chatStatus.textContent = '聊天列表加载失败，请重新打开「消息」页重试。';
    } finally {
      loadingChats = false;
    }
  }

  function updateChatSelection() {
    q('#chat-selected').textContent = '已选 ' + selected.size + ' / ' + maxSelected + ' 条';
    generateButton.disabled = generating || !selected.size;
    for (const input of chatList.querySelectorAll('input[type="checkbox"]')) {
      input.checked = selected.has(Number(input.value));
      input.disabled = generating || (!input.checked && selected.size >= maxSelected);
    }
  }

  function chatMessage(message) {
    const row = document.createElement('li');
    row.className = 'chat-message';
    const selectable = (message.message_type || 'text') === 'text' && message.text.trim();
    const entry = document.createElement(selectable ? 'label' : 'div');
    if (selectable) {
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = message.id;
      entry.append(input);
    } else {
      entry.className = 'chat-message-static';
    }
    const body = document.createElement('span');
    const meta = document.createElement('span');
    meta.className = 'chat-message-meta muted';
    meta.textContent = (message.sender_name || '未知发送者') + ' · ' +
      ((message.sent_at || '').replace('T', ' ').slice(0, 16) || '时间未知');
    const content = document.createElement('span');
    content.className = 'chat-message-text';
    content.textContent = message.text || '暂无文字内容';
    body.append(meta, content);
    entry.append(body);
    row.append(entry);
    return row;
  }

  async function loadChatMessages(chat, cursor, request) {
    const query = new URLSearchParams({source_platform: chat.source_platform, conversation_id: chat.conversation_id});
    if (cursor !== null) query.set('before_id', cursor);
    olderButton.disabled = true;
    chatStatus.textContent = '正在读取聊天记录…';
    try {
      const data = await get('/api/messages/recent?' + query);
      if (request !== chatRequest) return;
      q('#chat-history').hidden = !data.messages.length && !chatList.children.length;
      chatStatus.textContent = q('#chat-history').hidden ? '该聊天暂无消息。' : '';
      const previousHeight = chatList.scrollHeight;
      const previousTop = chatList.scrollTop;
      chatList.prepend(...data.messages.map(chatMessage));
      chatList.scrollTop = cursor === null ? chatList.scrollHeight :
        previousTop + chatList.scrollHeight - previousHeight;
      beforeId = data.before_id;
      olderButton.hidden = beforeId === null;
      updateChatSelection();
    } catch {
      if (request === chatRequest) chatStatus.textContent = '聊天记录加载失败，请刷新聊天重试。';
    } finally {
      if (request === chatRequest) olderButton.disabled = false;
    }
  }

  function openChat() {
    const request = ++chatRequest;
    beforeId = null;
    generating = false;
    selected.clear();
    chatList.replaceChildren();
    q('#chat-history').hidden = true;
    olderButton.hidden = true;
    generateStatus.textContent = '';
    aiSettings.hidden = true;
    chatStatus.textContent = '';
    const chat = conversations.get(chatSelect.value);
    if (chat) loadChatMessages(chat, null, request);
  }

  chatSelect.onchange = openChat;
  aiSettings.addEventListener('click', () => {
    requestAnimationFrame(() => q('#llm-settings').scrollIntoView({block: 'center'}));
  });
  q('#chat-refresh').onclick = () => {
    if (chatSelect.value) openChat();
    loadChats();
  };
  olderButton.onclick = () => loadChatMessages(conversations.get(chatSelect.value), beforeId, chatRequest);
  chatList.onchange = event => {
    if (event.target.type !== 'checkbox') return;
    const id = Number(event.target.value);
    if (!event.target.checked) selected.delete(id);
    else if (selected.size < maxSelected) selected.add(id);
    generateStatus.textContent = '';
    updateChatSelection();
  };
  document.addEventListener('chat-task-saved', () => {
    selected.clear();
    generateStatus.textContent = '';
    updateChatSelection();
  });
  generateButton.onclick = async () => {
    if (generating || !selected.size) return;
    const request = chatRequest;
    const ids = [...selected];
    generating = true;
    updateChatSelection();
    generateStatus.textContent = '正在整理所选消息…';
    aiSettings.hidden = true;
    try {
      const response = await fetch('/api/workbench/items/draft-from-messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message_ids: ids}),
        signal: AbortSignal.timeout(105000),
      });
      const data = await response.json();
      if (request !== chatRequest || page !== 'messages') return;
      if (!response.ok) {
        aiSettings.hidden = data.code !== 'ai_disabled';
        throw new Error(typeof data.detail === 'string' ? data.detail : '生成失败，请重试。');
      }
      if (data.draft) {
        generateStatus.textContent = '';
        openEditor(data.draft, ids);
      } else {
        generateStatus.textContent = '选中的消息里没有明确待办，可以补选相关消息后再试。';
      }
    } catch (error) {
      if (request === chatRequest) generateStatus.textContent = error.name === 'TimeoutError' ?
        'AI 响应超时，请重试。' : error.message || '生成失败，请重试。';
    } finally {
      if (request === chatRequest) {
        generating = false;
        if (page !== 'messages') generateStatus.textContent = '';
        updateChatSelection();
      }
    }
  };

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

  function showHash() {
    const hash = document.body.dataset.dshMode === 'todos' ? 'tasks' : location.hash.slice(1);
    show(hash.startsWith('task-') ? 'tasks' : hash);
  }
  showHash();
  window.addEventListener('hashchange', showHash);
  let refreshing = false;
  async function refreshVisiblePage() {
    if (document.hidden || refreshing || q('#editor').open) return;
    refreshing = true;
    try {
      if (page === 'home' && !q('#identity-form').contains(document.activeElement)) await home();
      if (page === 'tasks') await Promise.all([load({background: true}), status()]);
      if (page === 'system') await system();
    } finally {
      refreshing = false;
    }
  }

  setInterval(refreshVisiblePage, 15000);
  document.addEventListener('visibilitychange', refreshVisiblePage);
})();
