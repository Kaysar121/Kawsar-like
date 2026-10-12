from flask import Flask, jsonify, request, render_template
import aiohttp
import asyncio
import json
import time
import requests
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from byte import encrypt_api, Encrypt_ID
from visit_count_pb2 import Info
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))

# ================= TOTAL SETTINGS (EDIT HERE) =================
# Edit OB_VERSION once; Visit and Like both use it.
OB_VERSION = "OB55"

# Edit the server URL for each region here. LikeProfile is derived from the same host.
SERVER_URLS = {
    "BD":  "https://clientbp.ppmainecoonghj.com/GetPlayerPersonalShow",
    "IND": "https://client.ind.freefiremobile.com/GetPlayerPersonalShow",
    "BR":  "https://client.us.freefiremobile.com/GetPlayerPersonalShow",
    "US":  "https://client.us.freefiremobile.com/GetPlayerPersonalShow",
    "SAC": "https://client.us.freefiremobile.com/GetPlayerPersonalShow",
    "NA":  "https://client.us.freefiremobile.com/GetPlayerPersonalShow",
}

# ONE token/JWT API for both Visit and Like.
# The original app.py appends /token internally; no separate endpoint setting.
API_BASE_URL = "http://jwt-gen-vaibhav.up.railway.app"

# False: use token_<server>.json. True: generate tokens, save, then use them.
TOKEN_GENERATOR_ENABLED = False
AUTO_GENERATE_TOKENS_ON_START = False

MAX_CONCURRENT = 25
REQUEST_TIMEOUT = 60
JWT_WORKERS = 25
LIKE_CONCURRENCY = 100
TOTAL_VISIT_TARGET = 15000
VISIT_BATCH_SIZE = 500

SERVER_ACCOUNT_FILES = {
    "BD": "account_bd.txt", "IND": "account_ind.txt", "BR": "account_br.txt",
    "US": "account_us.txt", "SAC": "account_sac.txt", "NA": "account_na.txt",
}
SERVER_TOKEN_FILES = {
    "BD": "token_bd.json", "IND": "token_ind.json", "BR": "token_br.json",
    "US": "token_us.json", "SAC": "token_sac.json", "NA": "token_na.json",
}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.txt")
TOKEN_FILE = os.path.join(BASE_DIR, "token_bd.json")

# ================= TOKEN GENERATOR =================
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.txt")
TOKEN_FILE = os.path.join(BASE_DIR, "token_bd.json")


def _account_candidates(server_name="BD"):
    server = str(server_name).upper()
    candidates = [
        os.path.join(BASE_DIR, f"accounts_{server.lower()}.json"),
        os.path.join(BASE_DIR, f"accounts_{server}.json"),
        os.path.join(BASE_DIR, f"account_{server.lower()}.txt"),
        os.path.join(BASE_DIR, f"account_{server}.txt"),
    ]
    mapped = SERVER_ACCOUNT_FILES.get(server)
    if mapped:
        candidates.append(os.path.join(BASE_DIR, mapped))
    if server == "BD":
        candidates.append(ACCOUNTS_FILE)
    return list(dict.fromkeys(candidates))

def _token_path(server_name="BD"):
    server = str(server_name).strip().upper()
    # When generator is OFF, use the exact token_<server>.json file.
    return os.path.join(BASE_DIR, SERVER_TOKEN_FILES.get(server, f"token_{server.lower()}.json"))

def load_accounts(server_name="BD"):
    for path in _account_candidates(server_name):
        if not os.path.isfile(path):
            continue
        try:
            if path.lower().endswith('.json'):
                with open(path, encoding='utf-8') as f: raw = json.load(f)
                if isinstance(raw, dict): raw = raw.get('accounts', raw.get('data', []))
                accounts=[]
                for item in raw if isinstance(raw, list) else []:
                    if isinstance(item, dict):
                        uid=item.get('uid', item.get('id', item.get('account_id', '')))
                        password=item.get('password', item.get('pass', ''))
                        if str(uid).strip() and str(password).strip(): accounts.append((str(uid).strip(),str(password).strip()))
            else:
                accounts=[]
                with open(path, encoding='utf-8') as f:
                    for line in f:
                        line=line.strip()
                        if line and not line.startswith('#') and ':' in line:
                            uid,password=line.split(':',1)
                            if uid.strip() and password.strip(): accounts.append((uid.strip(),password.strip()))
            if accounts:
                print(f"Loaded {len(accounts)} accounts from {os.path.basename(path)}")
                return accounts
        except Exception as e: print(f"Account file error {path}: {e}")
    return []

def fetch_token(uid, password):
    try:
        # Same URL construction as the original app.py.
        url = f"{API_BASE_URL.rstrip('/')}/token?uid={urllib.parse.quote(str(uid))}&password={urllib.parse.quote(str(password))}"
        r = requests.get(url, timeout=REQUEST_TIMEOUT)

        if r.status_code != 200:
            print(f"API HTTP Error {r.status_code} for {uid}")
            return uid, None

        data = r.json()

        token = None

        if isinstance(data, dict):

            if "token" in data:
                token = data["token"]

            elif "data" in data and isinstance(data["data"], dict):
                token = data["data"].get("token")

            elif "jwt" in data:
                token = data["jwt"]

        if token:
            print(f"✅ Token OK: {uid}")
            return uid, token
        else:
            print(f"❌ Token Missing: {uid}")
            return uid, None

    except Exception as e:
        print(f"Token fetch error for {uid}: {e}")
        return uid, None


def save_tokens(tokens, server_name="BD"):
    token_list = [{"token": t} for t in tokens if t]

    with open(_token_path(server_name), "w", encoding="utf-8") as f:
        json.dump(token_list, f, indent=2)

    print(os.path.basename(_token_path(server_name)), "updated with", len(token_list), "tokens")


def generate_tokens(server_name="BD"):
    if not TOKEN_GENERATOR_ENABLED:
        print("⏸️ Token generator is disabled in TOTAL SETTINGS.")
        return {"total_accounts": 0, "tokens_generated": 0, "disabled": True}

    accounts = load_accounts(server_name)
    total_accounts = len(accounts)

    print(f"🚀 Generating Tokens | Total Accounts: {total_accounts}")

    if total_accounts == 0:
        print("No accounts found in accounts.txt")
        return {"total_accounts": 0, "tokens_generated": 0}

    success_count = 0
    success_tokens = []

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        future_to_uid = {executor.submit(fetch_token, uid, pwd): uid for uid, pwd in accounts}

        for future in as_completed(future_to_uid):
            uid, token = future.result()
            if token:
                success_count += 1
                success_tokens.append(token)

    if success_tokens:
        save_tokens(success_tokens, server_name)

    print(f"✅ Token generation finished | Success: {success_count}/{total_accounts}")

    return {
        "total_accounts": total_accounts,
        "tokens_generated": success_count
    }


# ================= VISIT API =================
def load_tokens(server_name):
    path = _token_path(server_name)
    try:
        with open(path, "r", encoding="utf-8") as f: data = json.load(f)
        if isinstance(data, dict): data = data.get("tokens", data.get("data", []))
        result=[]
        for item in data if isinstance(data, list) else []:
            if isinstance(item, str) and item not in ("", "N/A"): result.append(item)
            elif isinstance(item, dict):
                token=item.get("token") or item.get("jwt") or item.get("access_token")
                if token and token not in ("", "N/A"): result.append(token)
        return result
    except Exception as e:
        app.logger.error("Token load error for %s: %s", server_name, e); return []


def get_tokens_for_server(server_name):
    """Shared behavior: OFF reads token_<server>.json; ON generates then reads that file."""
    server = str(server_name).strip().upper()
    if TOKEN_GENERATOR_ENABLED:
        result = generate_tokens(server)
        tokens = load_tokens(server)
        if not tokens:
            app.logger.error("Token generation returned no usable tokens for %s: %s", server, result)
        return tokens
    return load_tokens(server)


def get_url(server_name):
    server = str(server_name).strip().upper()
    if server == "INDIA":
        server = "IND"
    return SERVER_URLS.get(server, SERVER_URLS["BD"])


def parse_protobuf_response(response_data):
    try:
        info = Info()
        info.ParseFromString(response_data)

        return {
            "uid": info.AccountInfo.UID if info.AccountInfo.UID else 0,
            "nickname": info.AccountInfo.PlayerNickname if info.AccountInfo.PlayerNickname else "",
            "likes": info.AccountInfo.Likes if info.AccountInfo.Likes else 0,
            "region": info.AccountInfo.PlayerRegion if info.AccountInfo.PlayerRegion else "",
            "level": info.AccountInfo.Levels if info.AccountInfo.Levels else 0
        }

    except Exception as e:
        app.logger.error(f"Protobuf parsing error: {e}")
        return None


async def visit(session, url, token, uid, data):
    headers = {
        "ReleaseVersion": OB_VERSION,
        "X-GA": "v1 1",
        "Authorization": f"Bearer {token}",
        "Host": url.replace("https://", "").split("/")[0]
    }

    try:
        async with session.post(url, headers=headers, data=data, ssl=False) as resp:
            if resp.status == 200:
                response_data = await resp.read()
                return True, response_data
            else:
                return False, None

    except Exception:
        return False, None


async def send_until_target_success(tokens, uid, server_name, target_success=None):

    if target_success is None:
        target_success = TOTAL_VISIT_TARGET

    url = get_url(server_name)
    connector = aiohttp.TCPConnector(limit=0, ssl=False)

    total_success = 0
    total_sent = 0
    batch_no = 0

    first_success_response = None
    player_info = None

    MAX_TOTAL_ATTEMPTS = target_success * 3

    async with aiohttp.ClientSession(connector=connector) as session:

        encrypted = encrypt_api("08" + Encrypt_ID(str(uid)) + "1801")
        data = bytes.fromhex(encrypted)

        print(f"🚀 STARTING VISIT | UID: {uid} | Target: {target_success}")

        while total_success < target_success and total_sent < MAX_TOTAL_ATTEMPTS:

            batch_no += 1
            remaining = target_success - total_success
            batch_size = min(remaining, VISIT_BATCH_SIZE)

            tasks = [
                asyncio.create_task(
                    visit(session, url, tokens[(total_sent + i) % len(tokens)], uid, data)
                )
                for i in range(batch_size)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_success = 0

            for result in results:

                if isinstance(result, Exception):
                    continue

                success, response_data = result

                if success and response_data:
                    batch_success += 1

                    if first_success_response is None:
                        first_success_response = response_data
                        player_info = parse_protobuf_response(response_data)

            total_success += batch_success
            total_sent += batch_size

            print(f"[BATCH {batch_no}] ✅ {batch_success} success | Total: {total_success}/{target_success}")

            await asyncio.sleep(0.4 if batch_success > batch_size * 0.6 else 1.2)

    return total_success, total_sent, player_info


# ================= ROUTES =================
@app.route('/')
def home():
    return render_template("index.html")


@app.route('/api')
def api_info():
    return jsonify({
        "status": "Kawsar Like - Visit API",
        "api_status": "running",
        "token_generator_enabled": TOKEN_GENERATOR_ENABLED,
        "server_urls": SERVER_URLS,
        "endpoints": {
            "/visit/BD/123456789": "Visit API (default target)",
            "/visit/BD/123456789/1000": "Visit API (custom target)",
            "/like/BD/123456789": "Like API",
            "/refresh": "Refresh visit tokens (only when token generator is enabled)",
            "/health": "Health status"
        }
    })


@app.route('/health')
def health():
    return jsonify({"status": "ok", "token_generator_enabled": TOKEN_GENERATOR_ENABLED,
                    "servers": list(SERVER_ACCOUNT_FILES.keys())})


@app.route('/visit/<string:server>/<int:uid>', methods=['GET'])
@app.route('/visit/<string:server>/<int:uid>/<int:target>', methods=['GET'])
@app.route('/<string:server>/<int:uid>', methods=['GET'])
@app.route('/<string:server>/<int:uid>/<int:target>', methods=['GET'])
def send_visits(server, uid, target=None):

    start_time = time.time()
    server = server.upper()

    tokens = get_tokens_for_server(server)

    if not tokens:
        return jsonify({"error": "No valid tokens found"}), 500

    target_success = target if target is not None else TOTAL_VISIT_TARGET

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        total_success, total_sent, player_info = loop.run_until_complete(
            send_until_target_success(tokens, uid, server, target_success)
        )

    finally:
        loop.close()

    end_time = time.time()
    time_taken = round(end_time - start_time, 2)

    visits_failed = total_sent - total_success
    efficiency = round((total_success / total_sent) * 100, 2) if total_sent > 0 else 0

    if player_info:

        response = {
            "success": True,
            "uid": player_info.get("uid", 0),
            "nickname": player_info.get("nickname", ""),
            "level": player_info.get("level", 0),
            "likes": player_info.get("likes", 0),
            "region": player_info.get("region", ""),
            "visits_success": total_success,
            "visits_failed": visits_failed,
            "visits_total": total_sent,
            "target_set": target_success,
            "efficiency": f"{efficiency}%",
            "time_taken": f"{time_taken}s"
        }

        return jsonify(response), 200

    return jsonify({"error": "Could not decode player information"}), 500


@app.route('/refresh', methods=['GET'])
@app.route('/refresh/<string:server>', methods=['GET'])
def refresh_tokens(server=None):
    if not TOKEN_GENERATOR_ENABLED:
        return jsonify({"message":"Token generator is disabled in TOTAL SETTINGS", "token_generator_enabled":False}), 403
    names=[server.upper()] if server else ["BD", "IND", "BR", "US", "SAC", "NA"]
    results=[generate_tokens(name) for name in names]
    return jsonify({"message":"Token refresh completed", "token_generator_enabled":True,
                    "results":results,
                    "total_accounts":sum(x.get("total_accounts",0) for x in results),
                    "tokens_generated":sum(x.get("tokens_generated",0) for x in results)})


# ================= LIKE API (added to the original Visit app.py) =================
def _account_file_path(server_name):
    filename = SERVER_ACCOUNT_FILES.get(server_name.upper())
    return os.path.join(BASE_DIR, filename) if filename else None


def _load_like_accounts(server_name):
    path = _account_file_path(server_name)
    if not path or not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            uid, password = line.split(":", 1)
            if uid.strip() and password.strip():
                out.append((uid.strip(), password.strip()))
    return out


def _like_encrypt(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return binascii.hexlify(cipher.encrypt(pad(plaintext, AES.block_size))).decode("ascii")


def _like_message(uid, region):
    import like_pb2
    message = like_pb2.like()
    message.uid = int(uid)
    message.region = region
    return message.SerializeToString()


def _like_profile_url(server_name):
    # Derive LikeProfile from the same per-region URL used by Visit.
    show_url = get_url(server_name)
    return show_url.rsplit("/", 1)[0] + "/LikeProfile"


def _get_player_info(uid, server_name, token):
    import like_count_pb2
    encrypted = encrypt_api("08" + Encrypt_ID(str(uid)) + "1801")
    data = bytes.fromhex(encrypted)
    url = get_url(server_name)
    headers = {"Authorization": f"Bearer {token}", "ReleaseVersion": OB_VERSION,
               "X-GA": "v1 1", "Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(url, headers=headers, data=data, timeout=REQUEST_TIMEOUT, verify=False)
    if response.status_code != 200:
        return None
    info = like_count_pb2.Info()
    info.ParseFromString(response.content)
    parsed = json.loads(MessageToJson(info))
    account = parsed.get("AccountInfo", {})
    found_uid = int(account.get("UID", 0) or 0)
    if not found_uid:
        return None
    return {"uid": found_uid, "likes": int(account.get("Likes", 0) or 0),
            "name": str(account.get("PlayerNickname", "Unknown"))}


async def _send_like_requests(uid, server_name, tokens):
    url = _like_profile_url(server_name)
    payload = bytes.fromhex(_like_encrypt(_like_message(uid, server_name)))
    headers = {"User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
               "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
               "Content-Type": "application/x-www-form-urlencoded", "X-Unity-Version": "2018.4.11f1",
               "X-GA": "v1 1", "ReleaseVersion": OB_VERSION}
    connector = aiohttp.TCPConnector(limit=LIKE_CONCURRENCY, ssl=False)
    sem = asyncio.Semaphore(LIKE_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        async def send(token):
            async with sem:
                try:
                    h = dict(headers)
                    h["Authorization"] = f"Bearer {token}"
                    async with session.post(url, data=payload, headers=h) as resp:
                        return resp.status
                except Exception:
                    return 0
        return await asyncio.gather(*(send(token) for token in tokens))


@app.route('/like/<string:server>/<int:uid>', methods=['GET'])
def send_likes(server, uid):
    server = server.upper()
    if server not in SERVER_ACCOUNT_FILES:
        return jsonify({"error": "Unsupported server", "allowed": list(SERVER_ACCOUNT_FILES)}), 400
    tokens = get_tokens_for_server(server)
    if not tokens:
        return jsonify({"error": "No valid tokens found"}), 500
    try:
        before = _get_player_info(uid, server, tokens[0])
        if before is None:
            return jsonify({"error": "Could not read player info", "uid": uid, "server_name": server}), 502
        statuses = asyncio.run(_send_like_requests(uid, server, tokens))
        after = _get_player_info(uid, server, tokens[0]) or before
        given = max(0, after["likes"] - before["likes"])
        return jsonify({"success": given > 0, "UID": after["uid"], "PlayerNickname": after["name"],
            "LikesbeforeCommand": before["likes"], "LikesafterCommand": after["likes"],
            "LikesGivenByAPI": given, "tokens_used": len(tokens), "tokens_generated": len(tokens),
            "requests_success": sum(1 for status in statuses if status == 200),
            "requests_total": len(statuses), "server_name": server, "status": 1 if given else 2})
    except Exception as exc:
        app.logger.exception("Like API failed")
        return jsonify({"error": "Like request failed", "detail": str(exc)}), 500


# ================= SERVER STARTUP / RESTART =================
def run_server():
    port = int(os.environ.get("PORT", 5000))
    if TOKEN_GENERATOR_ENABLED and AUTO_GENERATE_TOKENS_ON_START:
        for name in ("BD", "IND", "BR", "US", "SAC", "NA"):
            generate_tokens(name)
    while True:
        try:
            print("Starting Flask Server...")
            app.run(host="0.0.0.0", port=port, use_reloader=False)
        except Exception as e:
            print("Server crashed:", e)
            time.sleep(5)


# ================= RUN =================
if __name__ == "__main__":
    run_server()
