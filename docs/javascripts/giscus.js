/*
 * Giscus 评论系统集成
 * 基于 GitHub Discussions 的评论系统
 * 配置说明: 访问 https://giscus.app 获取配置
 */

document$.subscribe(function() {
  // 检查是否配置了 Giscus
  const giscusConfig = document.documentElement.getAttribute('data-giscus-config');
  if (!giscusConfig) {
    // 如果配置了 extra.giscus，则加载 Giscus
    const giscusScript = document.createElement('script');
    giscusScript.src = 'https://giscus.app/client.js';
    giscusScript.async = true;
    giscusScript.crossOrigin = 'anonymous';

    // 从配置中读取参数
    const config = typeof window.giscusConfig !== 'undefined'
      ? window.giscusConfig
      : {
          repo: 'cloudQuant/bt_api_py',
          repoId: '',
          category: 'Announcements',
          categoryId: '',
          mapping: 'pathname',
          strict: '0',
          reactionsEnabled: '1',
          emitMetadata: '0',
          inputPosition: 'bottom',
          theme: 'preferred_color_scheme',
          lang: 'zh-CN',
          loading: 'lazy'
        };

    // 设置 Giscus 属性
    for (const [key, value] of Object.entries(config)) {
      if (value) {
        const dataKey = key === 'repoId' ? 'data-repo-id' :
                        key === 'categoryId' ? 'data-category-id' :
                        'data-' + key.replace(/[A-Z]/g, m => '-' + m.toLowerCase());
        giscusScript.setAttribute(dataKey, value);
      }
    }

    // 将 Giscus 插入到页面底部的评论容器中
    const commentContainer = document.querySelector('[data-giscus-container]') ||
                             document.createElement('div');
    if (!document.querySelector('[data-giscus-container]')) {
      commentContainer.setAttribute('data-giscus-container', '');
      commentContainer.style.marginTop = '2rem';
      commentContainer.style.paddingTop = '2rem';
      commentContainer.style.borderTop = '1px solid var(--md-default-fg-color--lightest)';

      // 找到合适的插入位置（文章内容之后）
      const articleContent = document.querySelector('.md-content__inner');
      if (articleContent) {
        articleContent.appendChild(commentContainer);
      }
    }

    commentContainer.appendChild(giscusScript);
  }
});

// 监听主题切换，同步更新 Giscus 主题
window.addEventListener('theme-change', function(e) {
  const giscusFrame = document.querySelector('iframe[src*="giscus"]');
  if (giscusFrame) {
    const theme = e.detail.matches ? 'dark' : 'light';
    giscusFrame.contentWindow.postMessage({
      giscus: {
        setConfig: {
          theme: theme
        }
      }
    }, 'https://giscus.app');
  }
});
