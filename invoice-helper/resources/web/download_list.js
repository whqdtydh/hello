// resources/web/download_list.js
// 负责：渲染列表、暴露给 Python 的渲染/更新接口、进度条动画钩子

(function() {
  'use strict';

  const listEl = document.getElementById('download-list');
  const emptyEl = document.getElementById('empty-state');
  const countEl = document.getElementById('selected-count');
  const btnDownload = document.getElementById('btn-download');
  const progressBar = document.getElementById('progress-bar');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');

  let mails = [];
  let selectedIds = new Set();

  // ========== 对外暴露的接口（Python 调用） ==========
  window.renderDownloadList = function(mailArray) {
    mails = mailArray || [];
    selectedIds.clear();
    render();
    // 渲染完成后触发入场动画
    requestAnimationFrame(() => {
      // 强制重排确保初始状态生效
      document.querySelectorAll('.mail-row').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
      });
    });
  };

  window.updateSelectedCount = function(count) {
    selectedIds.size = count;
    countEl.textContent = `已选 ${count} 封`;
    btnDownload.disabled = count === 0;
  };

  window.showProgress = function(show, text) {
    progressBar.style.display = show ? 'flex' : 'none';
    if (text) progressText.textContent = text;
  };

  window.progressStart = function() {
    progressFill.className = 'progress-fill';
    progressFill.style.width = '0%';
    progressBar.style.display = 'flex';
    progressText.textContent = '准备中...';
    progressFill.offsetWidth;  // 强制重排
  };

  window.progressSet = function(percent, text) {
    const p = Math.max(0, Math.min(100, Math.round(percent)));
    progressFill.style.width = p + '%';
    if (text) progressText.textContent = text;
  };

  window.progressIndeterminate = function(text) {
    progressFill.className = 'progress-fill';
    progressFill.style.animation = 'progress-shine 1.5s linear infinite';
    if (text) progressText.textContent = text;
  };

  window.progressComplete = function(text, onComplete) {
    progressFill.className = 'progress-fill complete';
    if (text) progressText.textContent = text;
    if (onComplete && window[onComplete]) window[onComplete]();
  };

  window.progressError = function(text) {
    progressFill.className = 'progress-fill error';
    if (text) progressText.textContent = text;
  };

  window.progressPause = function(text) {
    progressFill.className = 'progress-fill paused';
    if (text) progressText.textContent = text;
  };

  window.progressHide = function() {
    progressBar.style.display = 'none';
  };

  // ========== 内部渲染 ==========
  function render() {
    if (mails.length === 0) {
      listEl.innerHTML = '';
      emptyEl.style.display = 'flex';
      return;
    }
    emptyEl.style.display = 'none';
    listEl.innerHTML = mails.map((m, i) => `
      <div class="mail-row" data-mailid="${escapeHtml(m.mailid)}" data-index="${i}">
        <input type="checkbox" class="mail-checkbox" ${selectedIds.has(m.mailid) ? 'checked' : ''}>
        <div class="mail-info">
          <div class="mail-subject">${escapeHtml(m.subject || '无主题')}</div>
          <div class="mail-meta">
            <span class="mail-time">${escapeHtml(m.time || '')}</span>
            <span class="mail-sender">${escapeHtml(m.sender || '')}</span>
            ${m.has_attach ? '<span class="mail-attach">📎 有附件</span>' : ''}
            ${m.size ? `<span class="mail-size">${formatSize(m.size)}</span>` : ''}
          </div>
        </div>
      </div>
    `).join('');

    // 绑定勾选事件
    listEl.querySelectorAll('.mail-checkbox').forEach((cb, idx) => {
      cb.addEventListener('change', () => {
        const mail = mails[idx];
        if (cb.checked) selectedIds.add(mail.mailid);
        else selectedIds.delete(mail.mailid);
        if (window.onSelectionChange) window.onSelectionChange(selectedIds.size);
      });
    });
  }

  function escapeHtml(str) {
    return String(str || '').replace(/[&<>"']/g, c => ({
      '&': '&', '<': '<', '>': '>', '"': '"', "'": '''
    }[c]));
  }

  function formatSize(bytes) {
    if (!bytes) return '';
    const k = 1024, sizes = ['B','KB','MB','GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
  }

  // 页面加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      // 确保 anime.js 就绪后自动跑入场动画（由 Python 侧触发）
    });
  } else {
    // 已加载完成
  }
})();