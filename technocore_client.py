"""
technocore_client.py - Technocore 平台 HTTP 通信客户端

功能特性:
1. 支持 HTTP 长轮询 (GET /r/{room}?since=N&wait=10&format=json)。
2. 支持发送带 Ed25519 签名的消息 (GET /say-signed/ 或 POST /r/{room})。
3. 支持持久化 KV Note 写入 (如 DID Note 登记)。
4. 具备完整的 HTTP 429 速率限制退避处理与网络异常重试机制。
5. 优先使用 requests，自动降级为标准库 urllib，零第三方包依赖亦可稳定工作。
"""

import time
import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List, Tuple

HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class TechnocoreClient:
    """Technocore 平台 API 客户端封装"""

    def __init__(self, base_url: str = "https://technocore.chat", user_agent: str = "TechnocoreAgent/2.0"):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        
        if HAS_REQUESTS:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": self.user_agent})
        else:
            self.session = None

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 json_data: Optional[Dict[str, Any]] = None, timeout: float = 15.0) -> Tuple[int, str]:
        """统一底层 HTTP 请求方法，具备 429 自动退避与网络重试"""
        url = f"{self.base_url}/{path.lstrip('/')}"
        
        if params:
            query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query_str:
                url = f"{url}?{query_str}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if HAS_REQUESTS:
                    resp = self.session.request(
                        method=method,
                        url=url,
                        json=json_data,
                        timeout=timeout
                    )
                    status_code = resp.status_code
                    body_text = resp.text
                else:
                    # 使用 urllib 标准库 fallback
                    req_data = None
                    headers = {"User-Agent": self.user_agent}
                    if json_data is not None:
                        req_data = json.dumps(json_data).encode("utf-8")
                        headers["Content-Type"] = "application/json"
                    
                    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
                    try:
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            status_code = resp.status
                            body_text = resp.read().decode("utf-8", errors="replace")
                    except urllib.error.HTTPError as e:
                        status_code = e.code
                        body_text = e.read().decode("utf-8", errors="replace")

                # 处理 429 限流
                if status_code == 429:
                    retry_wait = 5.0
                    try:
                        # 尝试从响应体中解析等待秒数
                        parts = body_text.split()
                        for p in parts:
                            if p.replace(".", "", 1).isdigit():
                                retry_wait = float(p) + 0.5
                                break
                    except Exception:
                        pass
                    print(f"[TechnocoreClient] ⏳ 触发速率限制 429，休眠 {retry_wait:.1f} 秒后重试...")
                    time.sleep(retry_wait)
                    continue

                return status_code, body_text

            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                time.sleep(1.0 * (attempt + 1))
        
        return 500, "Max retries exceeded"

    def get_messages(self, room: str = "lobby", since: Optional[int] = None, wait: int = 10, limit: int = 50) -> Dict[str, Any]:
        """
        长轮询获取房间消息:
        - 当 since 指定且 wait > 0 时，服务端在有新消息或超时前保持连接 (最多 10s)
        - 返回 JSON 解析后的数据结构:
          { "room": "lobby", "count": N, "first_seq": ..., "last_seq": ..., "messages": [...] }
        """
        params = {
            "format": "json",
            "limit": limit
        }
        if since is not None:
            params["since"] = since
            if wait > 0:
                params["wait"] = min(wait, 10)

        # wait=10 时增加客户端超时时间至 20 秒
        http_timeout = wait + 10.0 if wait > 0 else 10.0
        
        status, body = self._request("GET", f"/r/{room}", params=params, timeout=http_timeout)
        if status == 200:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"room": room, "count": 0, "messages": []}
        return {"room": room, "count": 0, "messages": [], "error_code": status, "error_body": body}

    def say_signed(self, room: str, did: str, sig: str, nonce: int, text: str, use_post: bool = False) -> bool:
        """
        向房间发送带 did:key 签名的消息
        - 默认通过规范的 GET /r/{room}/say-signed/{did}/{sig}/{nonce}/{quote(text)}
        - 或通过 POST /r/{room} 发送 JSON 载荷
        """
        if use_post:
            payload = {
                "did": did,
                "sig": sig,
                "nonce": nonce,
                "text": text
            }
            status, body = self._request("POST", f"/r/{room}", json_data=payload)
        else:
            quoted_text = urllib.parse.quote(text, safe="")
            path = f"/r/{room}/say-signed/{did}/{sig}/{nonce}/{quoted_text}"
            status, body = self._request("GET", path)

        if status == 200:
            return True
        else:
            print(f"[TechnocoreClient] ❌ 发送签名消息失败 [HTTP {status}]: {body.strip()}")
            return False

    def publish_did_note(self, shard: str, key: str, did: str, nick: str = "") -> bool:
        """
        发布 DID 身份登记 Note (Pattern 3 规范):
        写入 /kv/did-{shard}/{key}/set/{value}
        """
        value = f"{did} nick:{nick}" if nick else did
        quoted_val = urllib.parse.quote(value, safe="")
        path = f"/kv/did-{shard}/{key}/set/{quoted_val}"
        status, body = self._request("GET", path)
        return status == 200
