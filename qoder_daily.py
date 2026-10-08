#!/usr/bin/env python3
"""
🧭 Qoder 中国版 每日签到 - 青龙脚本
------------------------------------------------------------

📌 功能
   Qoder 中国版每日签到（活动领取制）：
   1. GET  /sash/api/v1/me/campaigns?forceRefresh=true  拉实时活动列表
   2. 找 actionType=CLAIM_BENEFIT 且 claimStatus=CLAIMABLE 的活动
   3. POST /sash/api/v1/me/campaigns/{campaignId}/claim  领取 credits
   4. 已领取（CLAIMED / replayed / 409 ALREADY_CLAIMED）识别为今日已领，不算失败

🔑 环境变量
   QODER_TOKENS   【必填】多账号用换行或 & 分隔，每条支持三种写法：
       access_token
       access_token|refresh_token
       备注|access_token|refresh_token
      access_token 形如 dt-xxxx；refresh_token 形如 drt-xxxx（可选，配了可自动续期）

   PUSHPLUS_TOKEN    微信推送通道（可选）
   BARK_URL        iOS 推送地址（可选）
   WECOM_WEBHOOK   企微群机器人地址或 key（可选）
   DINGTALK_WEBHOOK  钉钉机器人 webhook（可选）
   DINGTALK_SECRET   钉钉加签密钥（可选）

⌨️ 命令
   python qoder_daily.py                 正常签到
   python qoder_daily.py --only 2        只跑第 2 个账号
   python qoder_daily.py --refresh-only  只刷新 token（结果存入 qoder_tokens.json）
   python qoder_daily.py --no-notify     不推送

📄 依赖：requests
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time

import requests

import urllib3
urllib3.disable_warnings()

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

BASE = "https://openapi.qoder.com.cn"
CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
CAMPAIGNS_QUERY = "?forceRefresh=true"
DEVICE_REFRESH_PATH = "/api/v1/deviceToken/refresh"
JOB_REFRESH_PATH = "/api/v1/jobToken/refresh"
SASH_CLIENT_TYPE = "10"
SASH_USER_AGENT = "Qoder"
STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qoder_tokens.json")

ONLY = None
REFRESH_ONLY = False
NO_NOTIFY = False


def log(msg):
    print("%s %s" % (time.strftime("[%H:%M:%S]"), msg), flush=True)


def mask(text, keep=6):
    text = str(text or "")
    if len(text) <= keep + 4:
        return text
    return text[:keep] + "****" + text[-4:]


def split_accounts(raw):
    out = []
    for chunk in (raw or "").replace("&", "\n").splitlines():
        chunk = chunk.strip().strip('"\'')
        if chunk and not chunk.startswith("#"):
            out.append(chunk)
    return out


def request(method, url, headers=None, body=None, timeout=20, retries=3):
    last = None
    for attempt in range(retries):
        try:
            session = requests.Session()
            session.trust_env = False
            return session.request(
                method, url,
                headers=headers or {},
                json=body if body is not None else None,
                timeout=timeout, verify=False,
            )
        except Exception as error:
            last = error
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("请求失败: %s" % last)


def json_of(response):
    try:
        return response.json()
    except Exception:
        return {}


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def token_fp(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


# ---------- token 本地存储（续期会轮换 token，必须落盘才能二次运行） ----------

def load_store():
    try:
        with open(STORE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_store(store):
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False, indent=2)
    except Exception as error:
        log("⚠️ token 存储写入失败: %s" % error)


def apply_store(account):
    store = load_store()
    keys = [token_fp(account["refresh"])] if account.get("refresh") else []
    keys.append(token_fp(account["access"]))
    for key in keys:
        saved = store.get(key)
        if not isinstance(saved, dict):
            continue
        if saved.get("access"):
            account["access"] = saved["access"]
            account["refresh"] = saved.get("refresh", account.get("refresh", ""))
            return True
    return False


def persist(account):
    store = load_store()
    store[token_fp(account.get("refresh") or account["access"])] = {
        "access": account["access"],
        "refresh": account.get("refresh", ""),
        "note": account.get("note", ""),
        "at": int(time.time()),
    }
    save_store(store)


# ---------- 上游协议 ----------

def sash_headers(token):
    return {
        "authorization": "Bearer %s" % token,
        "cosy-clienttype": SASH_CLIENT_TYPE,
        "accept": "application/json",
        "accept-language": "zh-CN",
        "user-agent": SASH_USER_AGENT,
    }


def refresh_account(account):
    refresh = account.get("refresh", "")
    if not refresh:
        raise RuntimeError("账号没有 refresh_token，无法续期")
    path = JOB_REFRESH_PATH if refresh.startswith("jrt-") else DEVICE_REFRESH_PATH
    response = request("POST", BASE + path,
                       headers={"User-Agent": "qoder-local-proxy"},
                       body={"refresh_token": refresh})
    if response.status_code in (401, 403):
        raise RuntimeError("刷新令牌已失效或与地区不符，请重新登录")
    payload = json_of(response)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    token = first_text(data.get("device_token"), data.get("deviceToken"),
                       data.get("token"), data.get("access_token"))
    if not token:
        raise RuntimeError("续期响应缺少 access token（HTTP %s）" % response.status_code)
    new_refresh = first_text(data.get("refresh_token"), data.get("refreshToken")) or refresh
    account["access"] = token
    account["refresh"] = new_refresh
    persist(account)
    log("   🔄 token 已续期并保存")
    return True


def fetch_campaigns(account):
    response = request("GET", BASE + CAMPAIGNS_PATH + CAMPAIGNS_QUERY,
                       headers=sash_headers(account["access"]))
    if response.status_code in (401, 403) and account.get("refresh"):
        refresh_account(account)
        response = request("GET", BASE + CAMPAIGNS_PATH + CAMPAIGNS_QUERY,
                           headers=sash_headers(account["access"]))
    return response


def claim_campaign(account, campaign_id):
    headers = sash_headers(account["access"])
    headers["content-type"] = "application/json"
    headers["origin"] = BASE
    return request("POST", "%s%s/%s/claim" % (BASE, CAMPAIGNS_PATH, campaign_id),
                   headers=headers, body={})


def do_checkin(account):
    response = fetch_campaigns(account)
    if response.status_code in (401, 403):
        return False, "登录状态失效，重新登录一下吧该账号"
    payload = json_of(response)
    if response.status_code != 200:
        return False, "活动列表返回 HTTP %s" % response.status_code

    rows = payload.get("campaigns")
    if not isinstance(rows, list):
        data = payload.get("data")
        rows = data.get("campaigns") if isinstance(data, dict) else None
    rows = rows if isinstance(rows, list) else []

    target = None
    claimed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("actionType") != "CLAIM_BENEFIT":
            continue
        status = row.get("claimStatus")
        if status == "CLAIMABLE" and target is None:
            target = row
        elif status == "CLAIMED":
            claimed = True

    if target is None:
        return (False, "今日已领取") if claimed else (False, "当前没有可领取的签到活动")

    campaign_id = first_text(target.get("campaignId"), target.get("campaignKey"))
    if not campaign_id:
        return False, "活动数据里没有 campaignId，无法领取"

    claim = claim_campaign(account, campaign_id)
    body = json_of(claim)
    if claim.status_code == 409 or body.get("result") == "ALREADY_CLAIMED" or body.get("replayed") is True:
        return False, "今日已领取"
    if claim.status_code not in (200, 201):
        message = first_text(body.get("errorMessage"), body.get("message"), body.get("msg"))
        return False, "领取失败 HTTP %s %s" % (claim.status_code, message)

    benefit = body.get("benefit") if isinstance(body.get("benefit"), dict) else {}
    amount = benefit.get("amount", target.get("benefit", {}).get("amount") if isinstance(target.get("benefit"), dict) else None)
    days = None
    validity = benefit.get("validity") if isinstance(benefit.get("validity"), dict) else {}
    if validity.get("days") is not None:
        days = validity.get("days")
    text = "获得 %s credits" % (amount if amount is not None else "?")
    if days:
        text += "，%s 天有效" % days
    return True, text


# ---------- 推送 ----------

def post_json(url, payload, timeout=20):
    session = requests.Session()
    session.trust_env = False
    return session.post(url, json=payload, timeout=timeout, verify=False)


def _env(name):
    return os.environ.get(name, "").strip()


def notify_pushplus(title, content):
    token = _env("PUSHPLUS_TOKEN")
    if not token:
        return False
    try:
        text = (content[:18000] + "\n...(已截断)") if len(content) > 18000 else content
        answer = post_json("https://www.pushplus.plus/send",
                           {"token": token, "title": title, "content": text, "template": "txt"})
        return str(json_of(answer).get("code")) == "200"
    except Exception:
        return False


def notify_bark(title, content):
    endpoint = _env("BARK_URL").rstrip("/")
    if not endpoint:
        return False
    try:
        answer = post_json(endpoint, {"title": title, "body": content, "group": "Qoder"})
        return json_of(answer).get("code") == 200
    except Exception:
        return False


def notify_wecom(title, content):
    hook = _env("WECOM_WEBHOOK")
    if not hook:
        return False
    if not hook.startswith("http"):
        hook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + hook
    try:
        answer = post_json(hook, {"msgtype": "text",
                                  "text": {"content": ("%s\n%s" % (title, content))[:2000]}})
        return json_of(answer).get("errcode") == 0
    except Exception:
        return False


def notify_dingtalk(title, content):
    hook = _env("DINGTALK_WEBHOOK")
    if not hook:
        return False
    try:
        secret = _env("DINGTALK_SECRET")
        if secret:
            stamp = str(int(time.time() * 1000))
            digest = hmac.new(secret.encode(), ("%s\n%s" % (stamp, secret)).encode(),
                              hashlib.sha256).digest()
            hook += ("&" if "?" in hook else "?") + "timestamp=%s&sign=%s" % (
                stamp, requests.utils.quote(base64.b64encode(digest)))
        answer = post_json(hook, {"msgtype": "text",
                                  "text": {"content": "%s\n%s" % (title, content)}})
        return json_of(answer).get("errcode") == 0
    except Exception:
        return False


def notify_ql(title, content):
    try:
        from notify import send as panel_send
        panel_send(title, content)
        return True
    except Exception:
        pass
    endpoint = _env("QL_URL").rstrip("/")
    token = _env("QL_TOKEN")
    if not endpoint or not token:
        return False
    try:
        session = requests.Session()
        session.trust_env = False
        answer = session.post(endpoint + "/api/system/notify?token=" + token,
                              json={"title": title, "content": content}, timeout=15, verify=False)
        return str(json_of(answer).get("code")) in ("0", "200")
    except Exception:
        return False


def notify_all(title, content):
    handlers = (notify_pushplus, notify_bark, notify_wecom, notify_dingtalk, notify_ql)
    if not any(handler(title, content) for handler in handlers):
        log("（未配置推送渠道，本次只记录日志）")


# ---------- 账号解析与主流程 ----------

def looks_like_token(value):
    value = (value or "").strip()
    return (value.startswith(("dt-", "drt-", "jt-", "jrt-", "eyJ"))
            or value.count(".") >= 2 or len(value) > 80)


def parse_accounts(raw):
    accounts = []
    for index, line in enumerate(split_accounts(raw), 1):
        parts = [part.strip() for part in line.split("|")]
        note = "账号%d" % index
        if len(parts) >= 3:
            note, access, refresh = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            first, second = parts[0], parts[1]
            if looks_like_token(first) and looks_like_token(second):
                access, refresh = first, second          # access|refresh
            elif looks_like_token(second):
                note, access, refresh = first, second, ""  # 备注|access
            else:
                access, refresh = first, second          # access|（空 refresh）
        else:
            access, refresh = parts[0], ""
        if access:
            accounts.append({"note": note or ("账号%d" % index), "access": access, "refresh": refresh})
    return accounts


def main():
    global ONLY, REFRESH_ONLY, NO_NOTIFY
    args = sys.argv[1:]
    if "--refresh-only" in args:
        REFRESH_ONLY = True
    if "--no-notify" in args:
        NO_NOTIFY = True
    if "--only" in args:
        try:
            ONLY = int(args[args.index("--only") + 1])
        except Exception:
            ONLY = None

    accounts = parse_accounts(os.environ.get("QODER_TOKENS", ""))
    if not accounts:
        log("❌ 未配置 QODER_TOKENS（多条用换行或 & 分隔；每条: access_token 或 access_token|refresh_token）")
        return

    log("╔════════════════════════════════════╗")
    log("║ 🧭 Qoder 中国版 每日签到           ║")
    log("╚════════════════════════════════════╝")
    log("👥 账号数: %d" % len(accounts))

    report = []
    for index, account in enumerate(accounts, 1):
        if ONLY and index != ONLY:
            continue
        apply_store(account)
        log("👤 [%d] %s  token=%s" % (index, account["note"], mask(account["access"])))
        if REFRESH_ONLY:
            try:
                refresh_account(account)
                report.append("账号%d %s: ✅ token 续期成功" % (index, account["note"]))
            except Exception as error:
                report.append("账号%d %s: ❌ token 续期失败: %s" % (index, account["note"], error))
            continue
        try:
            success, message = do_checkin(account)
            icon = "✅" if success else "ℹ️"
            log("   %s %s" % (icon, message))
            report.append("账号%d %s: %s %s" % (index, account["note"], icon, message))
        except Exception as error:
            log("   ❌ 异常: %s" % error)
            report.append("账号%d %s: ❌ %s" % (index, account["note"], error))
        time.sleep(1.5)

    summary = "🧭 Qoder 签到报告\n" + "\n".join(report) + "\n🕐 " + time.strftime("%Y-%m-%d %H:%M")
    log(summary)
    if not NO_NOTIFY and not REFRESH_ONLY:
        notify_all("🧭 Qoder 中国版签到报告", summary)


if __name__ == "__main__":
    main()
