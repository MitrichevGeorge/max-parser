const originalSend = WebSocket.prototype.send;
WebSocket.prototype.send = function(...args) {
  WebSocket.prototype.send = originalSend;
  debugger;
  return originalSend.apply(this, args);
};



(function(instance) {
  if (!instance || typeof instance.cmd !== 'function') return;

  const SKIP_OPCODES = new Set([289, 5]);
  const DEBUG_OPCODES = new Set([17]);

  const originalCmd = instance.cmd;

  instance.cmd = async function(e, t, n = {}) {
    const opcodeDecimal = Number(e);
    
    let safePayload;
    try {
      safePayload = JSON.stringify(t);
    } catch {
      safePayload = String(t);
    }

    
    if (DEBUG_OPCODES.has(opcodeDecimal)) {
        console.log(`[Network DEBUG] Opcode: ${opcodeDecimal}`, safePayload);
        debugger;
    }
    else{
        console.log(`[Network Cmd Call] Opcode: ${opcodeDecimal}`, safePayload);
    }

    if (SKIP_OPCODES.has(opcodeDecimal)) {
      console.warn(`[Network Skip] Opcode ${opcodeDecimal} skipped`);
      return Promise.resolve({ skipped: true });
    }

    return originalCmd.apply(this, arguments);
  };
})(this);


window.addEventListener("message", e => {
    if (e.origin === "https://id.vk.ru")
        console.log(JSON.stringify(e.data));
}, true);
