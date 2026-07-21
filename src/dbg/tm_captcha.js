// ==UserScript==
// @name         MAX VK Captcha Native Solver
// @namespace    http://tampermonkey.net/
// @version      6.0
// @description  Решение VK Captcha через встроенные классы MAX web (OF / postMessage)
// @match        https://web.max.ru/*
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    const SERVER_URL = "http://127.0.0.1:18765";

    GM_registerMenuCommand("Launch UI", () => loadTailwind(launchUI));

    let tailwindReady = false;
    let pendingCallback = null;

    function loadTailwind(cb) {
        if (tailwindReady && window.tailwind) {
            cb();
            return;
        }
        pendingCallback = cb;

        if (document.getElementById("mcs-tailwind")) {
            if (window.tailwind) {
                tailwindReady = true;
                window.tailwind.refresh && window.tailwind.refresh();
                if (pendingCallback) pendingCallback();
                pendingCallback = null;
            }
            return;
        }

        if (!document.getElementById("mcs-styles")) {
            const style = document.createElement('style');
            style.id = "mcs-styles";
            style.textContent = `
                @keyframes mcsSlideIn { from { opacity: 0; transform: translateX(120%); } to { opacity: 1; transform: translateX(0); } }
                @keyframes mcsFadeIn { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: scale(1); } }
                @keyframes mcsGlow { 0%, 100% { box-shadow: 0 0 30px rgba(168, 85, 247, 0.3); } 50% { box-shadow: 0 0 50px rgba(217, 70, 239, 0.5); } }
                @keyframes mcsFloat { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(20px, -20px); } }
                .mcs-slide-in { animation: mcsSlideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1); }
                .mcs-fade-in { animation: mcsFadeIn 0.4s ease-out; }
                .mcs-glow { animation: mcsGlow 3s ease-in-out infinite; }
                .mcs-float-1 { animation: mcsFloat 8s ease-in-out infinite; }
                .mcs-float-2 { animation: mcsFloat 10s ease-in-out infinite reverse; }
                html, body { margin: 0 !important; padding: 0 !important; background: #050208 !important; }
                #max-captcha-overlay * { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
            `;
            document.head.appendChild(style);
        }

        const s = document.createElement('script');
        s.id = "mcs-tailwind";
        s.src = "https://cdn.tailwindcss.com";
        s.onload = () => {
            tailwindReady = true;
            const apply = () => {
                if (window.tailwind && window.tailwind.refresh) {
                    window.tailwind.refresh();
                }
                if (pendingCallback) {
                    pendingCallback();
                    pendingCallback = null;
                }
            };
            setTimeout(apply, 50);
        };
        document.head.appendChild(s);
    }

    function launchUI() {
        if (document.getElementById("max-captcha-overlay")) {
            if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();
            return;
        }

        Array.from(document.body.children).forEach(child => {
            if (child.id !== "max-captcha-overlay" && child.id !== "notification-container") {
                child.remove();
            }
        });

        document.body.style.cssText = "background: #050208; margin: 0; padding: 0; overflow: hidden;";

        const overlay = document.createElement("div");
        overlay.id = "max-captcha-overlay";
        overlay.className = "fixed inset-0 z-[999999] flex items-center justify-center overflow-auto mcs-fade-in";
        overlay.style.background = "radial-gradient(ellipse at top, #1e1b4b 0%, #0a0612 50%, #050208 100%)";
        overlay.innerHTML = `
            <div class="absolute inset-0 overflow-hidden pointer-events-none">
                <div class="absolute -top-40 -right-40 w-[500px] h-[500px] bg-purple-600/30 rounded-full blur-3xl mcs-float-1"></div>
                <div class="absolute -bottom-40 -left-40 w-[500px] h-[500px] bg-pink-600/25 rounded-full blur-3xl mcs-float-2"></div>
                <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[400px] bg-fuchsia-600/15 rounded-full blur-3xl mcs-float-1"></div>
            </div>
            <div class="relative w-full max-w-lg mx-4 my-8">
                <div class="bg-slate-900/40 backdrop-blur-2xl border border-slate-700/50 rounded-3xl shadow-2xl p-8 mcs-glow">
                    <div class="text-center mb-6">
                        <div class="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-gradient-to-br from-purple-500 via-fuchsia-500 to-pink-500 mb-5 shadow-2xl shadow-purple-500/50 relative">
                            <div class="absolute inset-0 rounded-3xl bg-gradient-to-br from-purple-500 via-fuchsia-500 to-pink-500 blur-xl opacity-60"></div>
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-10 h-10 text-white relative z-10" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                            </svg>
                        </div>
                        <h1 class="text-4xl font-black bg-gradient-to-r from-white via-purple-200 to-fuchsia-200 bg-clip-text text-transparent mb-2 tracking-tight">MAX Captcha Solver</h1>
                        <p class="text-slate-400 text-sm font-medium">Решение капчи VK</p>
                    </div>
                    <div id="captcha-content"></div>
                    <div class="mt-6 pt-6 border-t border-slate-800/50 text-center">
                        <button id="close-ui-btn" class="text-slate-400 hover:text-white text-sm transition-all inline-flex items-center gap-1.5 group px-4 py-2 rounded-xl hover:bg-slate-800/50">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 group-hover:-translate-x-1 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" /></svg>
                            Вернуться к MAX
                        </button>
                    </div>
                </div>
                <p class="text-center text-slate-600 text-xs mt-4 font-medium">Powered by MAX VK Captcha Solver v6.0</p>
            </div>
        `;
        document.body.appendChild(overlay);

        const notifContainer = document.createElement("div");
        notifContainer.id = "notification-container";
        notifContainer.className = "fixed top-6 right-6 z-[1000001] flex flex-col gap-3 pointer-events-none";
        document.body.appendChild(notifContainer);

        if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();

        renderGetButton();
        document.getElementById("close-ui-btn").addEventListener("click", closeUI);
    }

    function closeUI() {
        location.reload();
    }

    function showNotification(message, type = "info") {
        const container = document.getElementById("notification-container");
        if (!container) return;

        const styles = {
            success: { gradient: "from-emerald-500 to-teal-600", border: "border-emerald-300", icon: "✓", shadow: "shadow-emerald-500/50" },
            error: { gradient: "from-rose-500 to-red-600", border: "border-rose-300", icon: "✕", shadow: "shadow-rose-500/50" },
            info: { gradient: "from-blue-500 to-indigo-600", border: "border-blue-300", icon: "ℹ", shadow: "shadow-blue-500/50" }
        };

        const s = styles[type] || styles.info;
        const notif = document.createElement("div");
        notif.className = `pointer-events-auto bg-gradient-to-r ${s.gradient} ${s.shadow} border-l-4 ${s.border} text-white px-5 py-4 rounded-xl shadow-2xl flex items-center gap-3 min-w-[300px] max-w-md mcs-slide-in backdrop-blur-sm`;
        notif.innerHTML = `
            <span class="text-lg font-bold w-8 h-8 flex items-center justify-center bg-white/25 rounded-full flex-shrink-0 backdrop-blur-sm">${s.icon}</span>
            <span class="flex-1 text-sm font-semibold">${message}</span>
        `;
        container.appendChild(notif);

        setTimeout(() => {
            notif.style.transition = "all 0.4s cubic-bezier(0.4, 0, 1, 1)";
            notif.style.opacity = "0";
            notif.style.transform = "translateX(120%)";
            setTimeout(() => notif.remove(), 400);
        }, 4000);

        if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();
    }

    function renderGetButton() {
        const content = document.getElementById("captcha-content");
        if (!content) return;
        content.innerHTML = `
            <button id="get-captcha-btn" class="group relative w-full bg-gradient-to-r from-purple-600 via-fuchsia-600 to-pink-600 hover:from-purple-500 hover:via-fuchsia-500 hover:to-pink-500 text-white font-bold py-6 px-6 rounded-2xl transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98] shadow-2xl shadow-purple-500/50 flex items-center justify-center gap-3 overflow-hidden">
                <div class="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700"></div>
                <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6 group-hover:rotate-12 group-hover:scale-110 transition-all relative z-10" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                <span class="text-lg relative z-10">Получить</span>
            </button>
            <div class="mt-5 flex items-center justify-center gap-2 text-xs text-slate-500 font-medium">
                <div class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></div>
                <span>${SERVER_URL}</span>
            </div>
        `;
        document.getElementById("get-captcha-btn").addEventListener("click", handleGetCaptcha);
        if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();
    }

    function renderLoading() {
        const content = document.getElementById("captcha-content");
        content.innerHTML = `
            <div class="flex flex-col items-center justify-center py-20">
                <div class="relative w-20 h-20 mb-6">
                    <div class="absolute inset-0 rounded-full border-4 border-purple-500/20"></div>
                    <div class="absolute inset-0 rounded-full border-4 border-transparent border-t-purple-500 border-r-fuchsia-500 animate-spin"></div>
                    <div class="absolute inset-2 rounded-full border-4 border-transparent border-b-pink-500 border-l-purple-400 animate-spin" style="animation-direction: reverse; animation-duration: 1.5s;"></div>
                </div>
                <p class="text-slate-200 text-base font-bold">Запрос капчи у сервера...</p>
                <p class="text-slate-500 text-sm mt-1.5 font-medium">Подключение к ${SERVER_URL}</p>
            </div>
        `;
        if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();
    }

    async function handleGetCaptcha() {
        renderLoading();

        let captchaSrc = "";
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            const res = await fetch(`${SERVER_URL}/captcha-url`, { signal: controller.signal });
            clearTimeout(timeout);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (!data.url) throw new Error("URL не найден");
            captchaSrc = data.url;
        } catch (e) {
            showNotification("Сервер не отвечает", "error");
            renderGetButton();
            return;
        }

        renderCaptcha(captchaSrc);
    }

    function renderCaptcha(rawUrl) {
        const content = document.getElementById("captcha-content");

        const u = new URL(rawUrl);
        u.searchParams.set("autofocus", "1");
        u.searchParams.set("origin", location.origin);
        u.searchParams.set("variant", "popup");

        content.innerHTML = `
            <div class="bg-white rounded-2xl overflow-hidden shadow-2xl border border-slate-700/50">
                <iframe id="captcha-iframe" src="${u.href}" class="w-full h-[450px] border-0 block"></iframe>
            </div>
            <button id="cancel-captcha-btn" class="mt-4 w-full text-slate-400 hover:text-white text-sm py-2.5 transition-all rounded-xl hover:bg-slate-800/50 font-medium">
                Отмена
            </button>
        `;

        document.getElementById("cancel-captcha-btn").addEventListener("click", () => {
            window.removeEventListener("message", messageHandler);
            renderGetButton();
        });

        const messageHandler = async (e) => {
            if (!e.data || e.data?.type !== "vk-sak-sdk") return;

            if (e.data.handler === "VKCaptchaGetResult" || e.data.handler === "common:get_result") {
                const token = e.data.params?.token;
                if (token) {
                    window.removeEventListener("message", messageHandler);
                    await processToken(token);
                }
            }
        };

        window.addEventListener("message", messageHandler);
        if (window.tailwind && window.tailwind.refresh) window.tailwind.refresh();
    }

    async function processToken(token) {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            await fetch(`${SERVER_URL}/result`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token }),
                signal: controller.signal
            });
            clearTimeout(timeout);
            showNotification("Капча успешно пройдена!", "success");
        } catch (e) {
            showNotification("Не удалось отправить токен на сервер", "error");
        }
        renderGetButton();
    }
})();