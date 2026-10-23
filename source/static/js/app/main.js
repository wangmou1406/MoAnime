/* ============================================================
   动漫下载机 · 入口：把 boot() 挂到 DOMContentLoaded
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */

/* 局域网访问时可能是从 http://地址:端口/?token=xxx 进来的。
   服务端已经在这次响应里种了 cookie，这里把令牌从地址栏擦掉，
   免得它留在浏览器历史记录里。 */
function stripTokenFromUrl() {
  try {
    const url = new URL(location.href);
    if (!url.searchParams.has('token')) return;
    url.searchParams.delete('token');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  } catch (err) {
    /* 老浏览器不支持 URL/History API 就算了，不影响使用 */
  }
}

document.addEventListener('DOMContentLoaded', () => {
  stripTokenFromUrl();
  boot();
});
