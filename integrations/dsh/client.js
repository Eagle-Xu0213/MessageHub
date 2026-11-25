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
            const session = ctx.sidebarRight.mounted.getSnapshot();
            if (!session) throw new Error('No active session');
            if (id) rightTasks.set(session, id);
            const existing = ctx.sidebarRight.openTabs.getSnapshot().find(tab =>
              tab.sessionId === session && tab.kind === kind);
            if (existing) {
              ctx.sidebarRight.focus(existing.tabId);
              if (!ctx.sidebarRight.isExpanded()) ctx.sidebarRight.toggleExpanded();
            } else {
              ctx.sidebarRight.openTab(kind);
            }
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
