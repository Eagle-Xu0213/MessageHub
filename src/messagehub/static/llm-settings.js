(() => {
  const form = document.querySelector('#llm-settings');
  if (!form) return;
  const fields = document.querySelector('#llm-fields');
  const status = document.querySelector('#llm-status');
  const input = form.elements;
  const url = '/api/settings/llm';

  function show(config) {
    input.enabled.checked = config.enabled;
    input.base_url.value = config.base_url || '';
    input.model.value = config.model || '';
    input.api_key.value = '';
    input.api_key.placeholder = config.has_key
      ? '已保存，留空将保留原密钥'
      : '填写服务商提供的 API 密钥';
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const data = {
      enabled: input.enabled.checked,
      base_url: input.base_url.value,
      model: input.model.value,
      api_key: input.api_key.value,
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
      status.textContent = config.enabled ? '已保存，AI 增强已开启。' : '已保存，AI 增强已关闭。';
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
    status.textContent = config.has_key
      ? (config.enabled ? 'AI 增强已开启。' : '已保存密钥，AI 增强已关闭。')
      : '尚未配置 AI，当前使用本地规则整理任务。';
    fields.disabled = false;
  }).catch(() => {
    status.textContent = '无法读取 AI 配置，请通过本机地址打开工作台后刷新重试。';
  });
})();
