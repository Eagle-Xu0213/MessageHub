window.__ModuleLoader__.load({
  id: 'messagehub-dsh',
  factory(require) {
    const React = require('react');
    const {createElement: h, useEffect, useRef, useState} = React;
    const origin = 'http://127.0.0.1:8000';
    const channel = 'messagehub-dsh';
    const panel = 'messagehub-dsh';
    const kind = 'messagehub-todos';

    function apply(ctx) {
      const frames = new Map();
      let fullTask = null;
      const rightTasks = new Map();
      let missingSession = false;

      function send(frame, type, extra = {}) {
        frame?.postMessage({channel, type, ...extra}, origin);
      }

      function openTodos(id) {
        const sidebar = ctx.sidebarRight;
        const session = sidebar.mounted.getSnapshot();
        if (!session) throw new Error('No active session');
        if (id) rightTasks.set(session, id);
        const existing = sidebar.openTabs.getSnapshot().find(tab =>
          tab.sessionId === session && tab.kind === kind);
        if (existing) {
          sidebar.focus(existing.tabId);
          if (!sidebar.isExpanded()) sidebar.toggleExpanded();
        } else {
          sidebar.openTab(kind);
        }
        return session;
      }

      // DS Bot 0.3.x renders its own footer and does not expose an action slot.
      ctx.effect(() => {
        const agentButton = 'button[class*="_panelRow"]:is([aria-label="智能体"], [aria-label="Agents"])';
        const taskButton = 'button[class*="_panelRow"]:is([aria-label="任务看板"], [aria-label="Task Board"], [aria-label="Task board"])';
        const style = document.createElement('style');
        style.textContent = `
          body:has(button.bt-return) [class*="_panelEntry"]:has(> ${taskButton}),
          [class*="_panelEntry"]:has(> [data-messagehub-ds-bot]) > button:not([data-messagehub-ds-bot]) {
            display: none;
          }
        `;
        document.head.append(style);

        const bot = document.createElement('button');
        bot.type = 'button';
        bot.dataset.messagehubDsBot = '';
        bot.setAttribute('aria-label', 'DS Bot');
        bot.title = 'DS Bot';
        bot.addEventListener('click', () => document.querySelector('button.bt-return')?.click());

        const shortcut = document.createElement('button');
        shortcut.type = 'button';
        shortcut.className = 'bt-account';
        shortcut.title = '在右侧打开 MessageHub 待办';
        shortcut.setAttribute('aria-label', shortcut.title);
        const icon = document.createElement('span');
        icon.className = 'bt-you';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = '✓';
        shortcut.append(icon);
        const syncSession = () => { shortcut.disabled = !ctx.sidebarRight.mounted.getSnapshot(); };
        shortcut.addEventListener('click', () => {
          if (ctx.sidebarRight.mounted.getSnapshot()) openTodos();
        });

        function sync() {
          const footer = document.querySelector('.bt-leftcol-foot');
          if (footer && shortcut.parentElement !== footer) footer.append(shortcut);
          if (!footer && shortcut.isConnected) shortcut.remove();

          const native = document.querySelector('button.bt-return');
          const entry = native && document.querySelector(agentButton);
          if (!entry) {
            if (bot.isConnected) bot.remove();
            return;
          }
          const label = entry.querySelector('[class*="_panelTitle"]');
          if (bot.parentElement === entry.parentElement && bot.className === entry.className &&
              Boolean(bot.querySelector('[class*="_panelTitle"]')) === Boolean(label)) return;
          bot.className = entry.className;
          const nativeMark = native.querySelector('.bt-mark');
          const mark = document.createElement('span');
          mark.setAttribute('aria-hidden', 'true');
          if (nativeMark) mark.append(nativeMark.cloneNode(true));
          else mark.textContent = '↗';
          bot.replaceChildren(mark);
          if (label) {
            const title = document.createElement('span');
            title.className = label.className;
            title.textContent = 'DS Bot';
            bot.append(title);
          }
          entry.before(bot);
        }

        const observer = new MutationObserver(sync);
        observer.observe(document.body, {childList: true, subtree: true});
        const unsubscribe = ctx.sidebarRight.mounted.subscribe(syncSession);
        sync();
        syncSession();
        return () => {
          observer.disconnect();
          unsubscribe();
          style.remove();
          bot.remove();
          shortcut.remove();
        };
      });

      function navigate(mode, target, id) {
        if (mode === 'todos' && target === 'full') {
          fullTask = id;
          ctx.layout.selectPanel(panel);
          for (const [frame, info] of frames) {
            if (info.mode === 'full' && id) send(frame, 'focus', {id});
          }
          return;
        }
        if (mode !== 'full' || target !== 'todos') return;
        ctx.layout.selectPanel(null);
        let attempts = 0;
        function open() {
          try {
            const session = openTodos(id);
            requestAnimationFrame(() => {
              for (const [frame, info] of frames) {
                if (info.mode === 'todos' && info.session === session && info.visible() && id) {
                  send(frame, 'focus', {id});
                  rightTasks.delete(session);
                }
              }
            });
          } catch {
            if (++attempts < 5) {
              setTimeout(open, 60);
            } else {
              missingSession = true;
              ctx.layout.selectPanel(panel);
              for (const [frame, info] of frames) {
                if (info.mode === 'full') send(frame, 'notice');
              }
            }
          }
        }
        open();
      }

      ctx.effect(() => {
        function receive(event) {
          const data = event.data;
          const info = frames.get(event.source);
          if (event.origin !== origin || !info || data?.channel !== channel) return;
          if (data.type === 'ready') {
            info.ready();
            const id = info.mode === 'full' ? fullTask : rightTasks.get(info.session);
            if (id) {
              send(event.source, 'focus', {id});
              if (info.mode === 'full') fullTask = null;
              else rightTasks.delete(info.session);
            }
            if (info.mode === 'full' && missingSession) {
              send(event.source, 'notice');
              missingSession = false;
            }
          }
          if (data.type === 'changed') {
            for (const frame of frames.keys()) {
              if (frame !== event.source) send(frame, 'refresh');
            }
          }
          if (data.type === 'navigate') {
            const id = Number.isSafeInteger(data.id) && data.id > 0 ? data.id : null;
            navigate(info.mode, data.target, id);
          }
        }
        window.addEventListener('message', receive);
        return () => window.removeEventListener('message', receive);
      });

      function Frame({mode, session}) {
        const ref = useRef(null);
        const [attempt, setAttempt] = useState(0);
        const [ready, setReady] = useState(false);
        const [unavailable, setUnavailable] = useState(false);
        const path = mode === 'full' ? '/?embed=dsh' : '/dsh/todos?embed=dsh';

        useEffect(() => {
          const element = ref.current;
          const frame = element?.contentWindow;
          if (!frame) return;
          const timeout = setTimeout(() => setUnavailable(true), 6000);
          frames.set(frame, {
            mode, session,
            visible: () => element.getClientRects().length > 0,
            ready: () => {
              clearTimeout(timeout);
              setReady(true);
              setUnavailable(false);
            },
          });
          const observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) send(frame, 'refresh');
          });
          observer.observe(element);
          return () => {
            clearTimeout(timeout);
            observer.disconnect();
            frames.delete(frame);
          };
        }, [mode, session, attempt]);

        return h('div', {style: {width: '100%', height: '100%', minHeight: 0}},
          !ready && h('div', {style: {padding: '16px'}, role: 'status'},
            unavailable ? '无法连接本机 MessageHub。' : '正在连接 MessageHub…',
            unavailable && h('button', {type: 'button', onClick: () => {
              setUnavailable(false);
              setAttempt(value => value + 1);
            }}, '重试')),
          h('iframe', {
            key: attempt,
            ref,
            title: mode === 'full' ? 'MessageHub 工作台' : 'MessageHub 待办',
            src: origin + path,
            sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals',
            style: {display: ready ? 'block' : 'none', width: '100%', height: '100%', border: 0},
            onLoad: () => send(ref.current?.contentWindow, 'hello'),
          }));
      }

      ctx.effect(() => ctx.slots.inject('main', () =>
        ctx.slots.register({name: 'main', key: panel}, () => h(Frame, {mode: 'full'}))));
      ctx.effect(() => ctx.slots.inject('sidebar.panellist', () =>
        ctx.slots.register({
          name: 'sidebar.panellist', id: panel, order: 50, label: () => 'MessageHub 工作台',
        }, () => h('span', {'aria-hidden': true}, '✓'))));
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: panel, kind, priority: 'extension', title: () => '待办',
        guide: [{id: 'open', order: 50, title: () => '待办', description: () => '查看 MessageHub 待办'}],
      }));
      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () =>
        ctx.slots.register({
          name: 'sidebar.right.pane.tab', key: panel,
          inject: session => ({session}),
        }, ({session}) => h(Frame, {mode: 'todos', session}))));
    }

    return {inject: ['slots', 'layout', 'sidebarRightTabs', 'sidebarRight'], apply};
  },
  meta: () => ({}),
});
