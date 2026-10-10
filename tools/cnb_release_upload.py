"""上传文件到 CNB Release 附件（三步流程）。

参照官方附件插件 cnbcool/attachments 的实现：
  1. POST /{slug}/-/releases/{release_id}/asset-upload-url
     → 得到 upload_url（预签名，直传对象存储）与 verify_url（确认回调）
  2. PUT upload_url                 上传文件二进制
  3. GET verify_url                 确认上传，附件才正式挂到 Release

用法：
  python tools/cnb_release_upload.py <slug> <release_id> <文件路径> [--tag v0.5.1]

token 读取顺序：--token 参数 > 环境变量 CNB_TOKEN > ~/.cnb/token 的 access_token
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API_ENDPOINT = "https://api.cnb.cool"


def load_token(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("CNB_TOKEN")
    if env:
        return env
    path = pathlib.Path.home() / ".cnb" / "token"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"读取 token 失败：{exc}") from exc
    token = data.get("access_token")
    if not token:
        raise SystemExit("token 文件中没有 access_token 字段")
    return token


def request(url: str, token: str | None = None, method: str = "GET", body: dict | None = None):
    """发 HTTP 请求；返回 (status, 原始字节)。

    坑：Accept 必须用 application/json。
    实测 `Accept: application/vnd.cnb.api+json` 会被拒（errcode 406，
    提示 "either of 'application/json' or 'application/vnd.cnb.api+json' content type supported"），
    而 Content-Type / Accept 均用 application/json 时一切正常。
    """
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get_upload_url(slug: str, release_id: str, asset_name: str, size: int, token: str):
    url = f"{API_ENDPOINT}/{slug}/-/releases/{release_id}/asset-upload-url"
    status, raw = request(url, token, "POST", {"asset_name": asset_name, "size": size, "ttl": 0})
    if status not in (200, 201):
        raise SystemExit(f"获取上传链接失败 HTTP {status}: {raw[:400].decode('utf-8', 'replace')}")
    return json.loads(raw)


def put_file(upload_url: str, path: pathlib.Path) -> int:
    """直传对象存储。注意：不要手动设置 Content-Length，交给库处理。"""
    size = path.stat().st_size
    req = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def confirm_upload(verify_url: str, token: str) -> tuple[int, bytes]:
    """确认上传。

    两个坑：
      1. 必须用 POST —— 官方插件 request() 的 method 默认值就是 POST，
         用 GET 会得到 404 Resource not found。
      2. verify_url 必须原样使用——其中已含 URL 编码后的对象键，不可再编码。
    """
    return request(verify_url, token, "POST")


def main() -> int:
    parser = argparse.ArgumentParser(description="上传文件到 CNB Release 附件")
    parser.add_argument("slug", help="仓库路径，如 TJ-coding/gold-pet")
    parser.add_argument("release_id", help="Release ID")
    parser.add_argument("file", help="待上传文件路径")
    parser.add_argument("--token", default=None, help="CNB API Token")
    parser.add_argument("--tag", default=None, help="仅用于打印下载链接")
    args = parser.parse_args()

    token = load_token(args.token)
    path = pathlib.Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    size = path.stat().st_size
    print(f"文件：{path.name}  大小：{size:,} 字节 ({size / 1024 / 1024:.1f} MB)")
    print("-" * 62)

    print("[1/3] 获取上传链接……")
    info = get_upload_url(args.slug, args.release_id, path.name, size, token)
    upload_url = info.get("upload_url")
    verify_url = info.get("verify_url")
    if not upload_url or not verify_url:
        raise SystemExit(f"返回缺少必要字段：{info}")
    print(f"      链接有效期 {info.get('expires_in_sec', '?')} 秒")

    print("[2/3] 上传文件……")
    status = put_file(upload_url, path)
    print(f"      HTTP {status}")
    if status != 200:
        raise SystemExit(f"上传失败 HTTP {status}")

    print("[3/3] 确认上传……")
    status, raw = confirm_upload(verify_url, token)
    print(f"      HTTP {status}")
    if status != 200:
        raise SystemExit(f"确认失败：{raw[:400].decode('utf-8', 'replace')}")

    print("-" * 62)
    print("上传成功。")
    if args.tag:
        print(f"下载地址：https://cnb.cool/{args.slug}/-/releases/download/{args.tag}/{path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
