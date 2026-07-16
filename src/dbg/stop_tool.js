const originalSend = WebSocket.prototype.send;
function toBase64(data) {
  if (data instanceof ArrayBuffer) {
    const bytes = new Uint8Array(data);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }
  if (ArrayBuffer.isView(data)) {
    return toBase64(data.buffer);
  }
  if (data instanceof Blob) {
    data.arrayBuffer().then(buf => {
      console.log('%c[WS Blob Base64]:', 'color: #00ff00', toBase64(buf));
    });
    return '[Blob - асинхронное чтение...]';
  }
  return data;
}
WebSocket.prototype.send = function(...args) {
  console.group('WebSocket.send');
  console.trace();
  const formattedPayload = toBase64(args[0]);
  console.log('data:', formattedPayload);
  console.log(Date.now());
  console.groupEnd();
  debugger;
  return originalSend.apply(this, args);
};