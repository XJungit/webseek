"""状态存储: 每个 URL 的哈希与时间戳, 变化快照."""
import json
import os
import time
from datetime import datetime, timezone, timedelta


class Storage:
    def __init__(self, state_file, snapshot_dir):
        self.state_file = state_file
        self.snapshot_dir = snapshot_dir
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)) or ".", exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_file)

    def get(self, url):
        return self.data.get(url)

    def record_ok(self, url, digest, content):
        now = time.time()
        entry = self.data.setdefault(url, {})
        changed = entry.get("hash") != digest
        if changed:
            entry["hash"] = digest
            entry["last_change"] = now
            entry["content"] = content
        entry["last_seen"] = now
        return changed

    def save_snapshot(self, url, content, changed):
        if not changed:
            return None
        os.makedirs(self.snapshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe = url.replace("https://", "").replace("http://", "")
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe)[:80]
        path = os.path.join(self.snapshot_dir, f"{safe}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"URL: {url}\nTIME: {ts}\n\n{content}")
        return path

    def old_content(self, url):
        entry = self.data.get(url)
        return entry.get("content") if entry else None

    def record_fallback(self, url, note):
        """记录页面失效(回退到首页), 用哨兵哈希避免反复告警; 页面恢复时算作变化."""
        now = time.time()
        entry = self.data.setdefault(url, {})
        sentinel = "FALLBACK:" + note
        changed = entry.get("hash") != sentinel
        if changed:
            entry["hash"] = sentinel
            entry["last_change"] = now
        entry["last_seen"] = now
        entry["fallback"] = True
        return changed

    @staticmethod
    def fmt_time(ts):
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")
