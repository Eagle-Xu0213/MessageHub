(() => {
  const form = document.querySelector('#email-settings');
  const fields = document.querySelector('#email-fields');
  const status = document.querySelector('#email-status');
  const input = form.elements;
  const url = '/api/settings/email';

  function show(config) {
    input.enabled.checked = config.enabled;
    input.urgent_enabled.checked = config.urgent_enabled;
    input.digest_frequency.value = config.digest_frequency;
    input.host.value = config.host;
    input.port.value = config.port;
    input.user.value = config.user;
    input.recipient.value = config.to;
    input.password.value = '';
    input.password.placeholder = config.has_password ? '已保存，留空将保留原授权码' : '填写邮箱服务提供的授权码';
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const data = {
      enabled: input.enabled.checked,
      urgent_enabled: input.urgent_enabled.checked,
      digest_frequency: input.digest_frequency.value,
      host: input.host.value,
      port: input.port.value,
      user: input.user.value,
      pass: input.password.value,
      to: input.recipient.value,
    };
    fields.disabled = true;
    status.textContent = '正在保存…';
    try {
      const response = await fetch(url, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
      });
      const config = await response.json();
      if (!response.ok) throw new Error(config.detail || '保存失败');
      show(config);
      status.textContent = config.enabled ? '已保存，邮件通知已开启。' : '已保存，邮件通知已关闭。';
    } catch (error) {
      status.textContent = error.message || '保存失败，请稍后重试。';
    } finally {
      fields.disabled = false;
    }
  });

  fetch(url).then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(config => {
    show(config);
    status.textContent = config.enabled ? '邮件通知已开启。' : '邮件通知已关闭。';
    fields.disabled = false;
  }).catch(() => {
    status.textContent = '无法读取邮件配置，请通过本机地址打开工作台后刷新重试。';
  });
})();
