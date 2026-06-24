import json

URL = "wss://ws-api.oneme.ru/websocket"
HEADERS = {
    "Host": "ws-api.oneme.ru",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept" : "*/*",
    "Accept-Language" : "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7",
    "Accept-Encoding" : "gzip, deflate, br, zstd",
    "Sec-WebSocket-Version" : "13",
    "Origin" : "https://web.max.ru",
    "Sec-WebSocket-Extensions" : "permessage-deflate",
    "Sec-Fetch-Storage-Access" : "none",
    "Sec-GPC" : "1",
    "Connection" : "Upgrade",
    "Sec-Fetch-Dest" : "empty",
    "Sec-Fetch-Mode" : "websocket",
    "Sec-Fetch-Site" : "cross-site",
    "Pragma" : "no-cache",
    "Cache-Control" : "no-cache",
    "Upgrade" : "websocket",
}
DEFAULT_USER_AGENT = {
    "deviceType": "WEB",
    "pushDeviceType": "WEBPUSH",
    "locale": "en",
    "deviceLocale": "en",
    "osVersion": "Linux",
    "deviceName": "Firefox",
    "headerUserAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "appVersion": "26.6.19",
    "screen": "1200x1920 1.0x",
    "timezone": "Europe/Moscow"
}

def get_device_payload(device_id: str) -> str:
    data = {
        "ver": 11,
        "cmd": 0,
        "seq": 0,
        "opcode": 6,
        "payload": {
            "userAgent": DEFAULT_USER_AGENT,
            "deviceId": device_id
        }
    }
    return json.dumps(data)

def get_auth_payload(token: str, chats_count: int = 60) -> str:
    data = {
        "ver": 11,
        "cmd": 0,
        "seq": 1,
        "opcode": 19,
        "payload": {
            "token": token,
            "chatsCount": chats_count,
            "interactive": True,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": -1,
            "draftsSync": 0
        }
    }
    return json.dumps(data)