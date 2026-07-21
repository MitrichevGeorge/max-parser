(function () {
  if (window.__vkProxyInjected) return;
  window.__vkProxyInjected = true;

  const DOMAINS = __PROXIED_DOMAINS__;
  const PROXY_PREFIX = location.origin + '/proxy/';

  function toProxy(url) {
    const s = String(url);
    for (const d of DOMAINS) {
      if (s.includes(d)) {
        return PROXY_PREFIX + s.replace(/^https?:\/\//, '');
      }
    }
    return null;
  }

  const originalFetch = window.fetch;
  window.fetch = async function (input, init) {
    let url = '';
    if (typeof input === 'string') url = input;
    else if (input && input.url) url = input.url;

    const proxied = toProxy(url);
    if (proxied) {
      input = typeof input === 'string' ? proxied : new Request(proxied, input);
      init = init
        ? Object.assign({}, init, { credentials: 'omit' })
        : { credentials: 'omit' };
    }

    return originalFetch.call(this, input, init);
  };

  const OriginalXHR = window.XMLHttpRequest;
  function ProxyXHR() {
    const xhr = new OriginalXHR();
    const originalOpen = xhr.open;
    xhr.open = function (method, url, ...rest) {
      const proxied = toProxy(url);
      return originalOpen.call(this, method, proxied || url, ...rest);
    };
    return xhr;
  }
  ProxyXHR.prototype = OriginalXHR.prototype;
  Object.setPrototypeOf(ProxyXHR, OriginalXHR);
  window.XMLHttpRequest = ProxyXHR;

  const originalSendBeacon = navigator.sendBeacon?.bind(navigator);
  if (originalSendBeacon) {
    navigator.sendBeacon = (url, data) =>
      originalSendBeacon(toProxy(url) || url, data);
  }
})();