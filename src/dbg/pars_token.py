import base64
import json

def parse_jwe_header(jwe_str):
    parts = jwe_str.split('.')
    header_b64 = parts[0]
    
    rem = len(header_b64) % 4
    if rem > 0:
        header_b64 += '=' * (4 - rem)
        
    header_bytes = base64.urlsafe_b64decode(header_b64)
    header_json = json.loads(header_bytes.decode('utf-8'))
    return header_json

while True:
    try:
        token = input("token -> ").strip()
        if not token:
            continue
        
        print(json.dumps(parse_jwe_header(token), indent=4))
    except KeyboardInterrupt, EOFError:
        print("\nbye")
        break
    except Exception as err:
        print(f"Error while parsing token: {type(err).__name__} - {err}")