"""夸克网盘客户端封装 — 基于 quark_client 库"""

import random
import string
import time
from quark_client import QuarkClient as _QuarkClient


class QuarkClient:
    """封装 quark_client 的核心操作"""

    def __init__(self, cookie: str):
        self.cookie = cookie
        self.client = _QuarkClient(cookies=cookie, auto_login=True)

    def list_folder(self, folder_id: str = "0", page: int = 1, size: int = 50) -> list:
        """列出指定文件夹的内容"""
        try:
            result = self.client.list_files(folder_id=folder_id, page=page, size=size)
            files = result.get("data", {}).get("list", []) if isinstance(result, dict) else []
            return [
                {
                    "file_id": f.get("fid") or f.get("file_id", ""),
                    "name": f.get("file_name") or f.get("name", ""),
                    "is_dir": bool(f.get("dir") or f.get("is_dir")),
                }
                for f in files
            ]
        except Exception as e:
            raise RuntimeError(f"列出文件夹失败: {e}")

    def create_share(
        self,
        file_ids: list,
        title: str = "分享",
        password: str = None,
        expire_days: int = 30,
        visitor_limit: int = 1,
    ) -> dict:
        """创建分享链接，返回 {share_url, password, share_id}

        Args:
            file_ids: 文件/文件夹 ID 列表
            title: 分享标题
            password: 提取码（None 则自动生成 4 位数字）
            expire_days: 有效期天数（0=永久）
            visitor_limit: 访问人数限制（1=仅1人可访问）
        """
        if password is None:
            password = self._generate_password()
        try:
            # 直接调底层 API，传完整参数（绕过库的有限封装）
            data = {
                "fid_list": file_ids,
                "title": title,
                "url_type": 2 if visitor_limit == 1 else 1,  # 2=限制访问
                "expired_type": 1 if expire_days == 0 else 2,
                "passcode": password,
            }
            if expire_days > 0:
                data["expired_at"] = int((time.time() + expire_days * 24 * 3600) * 1000)

            # 尝试传入访问限制
            if visitor_limit > 0:
                data["share_limit"] = visitor_limit

            response = self.client.api_client.post("share", json_data=data)

            if not isinstance(response, dict) or response.get("status") != 200:
                msg = response.get("message", "未知错误") if isinstance(response, dict) else str(response)
                raise RuntimeError(f"创建分享失败: {msg}")

            task_id = response.get("data", {}).get("task_id")
            if not task_id:
                raise RuntimeError("无法获取分享任务ID")

            # 轮询等待任务完成
            for retry in range(10):
                task_resp = self.client.api_client.get(
                    "task",
                    params={"task_id": task_id, "retry_index": retry},
                )
                if isinstance(task_resp, dict) and task_resp.get("status") == 200:
                    task_data = task_resp.get("data", {})
                    if task_data.get("status") == 2:  # 完成
                        share_id = task_data.get("share_id")
                        if share_id:
                            # 获取分享详情
                            detail_resp = self.client.api_client.post(
                                "share/password",
                                json_data={"share_id": share_id},
                            )
                            if isinstance(detail_resp, dict):
                                raw_url = detail_resp.get("data", {}).get("share_url", f"https://pan.quark.cn/s/{share_id}")
                                # 拼接提取码到 URL，实现自动填充
                                share_url = f"{raw_url}?pwd={password}"
                                return {
                                    "share_url": share_url,
                                    "password": password,
                                    "share_id": share_id,
                                }
                    elif task_data.get("status") == 3:  # 失败
                        raise RuntimeError(f"分享任务失败: {task_data.get('message', '')}")
                time.sleep(1)

            raise RuntimeError("创建分享超时")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"创建分享失败: {e}")

    @staticmethod
    def _generate_password(length: int = 4) -> str:
        """生成随机提取码"""
        return "".join(random.choices(string.digits, k=length))
