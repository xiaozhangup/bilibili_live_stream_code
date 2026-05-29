import logging
import asyncio
import threading
from backend.bilibili_api import BilibiliApi
from backend.config import Config
from backend.state import SessionState
from backend.services.window_service import WindowService
from backend.services.user_service import UserService
from backend.services.live_service import LiveService
from backend.services.auth_service import AuthService
from backend.services.danmu_service import DanmuService
from backend.services.schedule_service import ScheduleService
from backend.services.status_api_service import StatusApiService

logger = logging.getLogger("ApiService")

class FrontendLogHandler(logging.Handler):
    """自定义日志处理器，将日志发送到前端"""
    def __init__(self, window_service):
        super().__init__()
        self.window_service = window_service

    def emit(self, record):
        try:
            msg = self.format(record)
            # 避免在主线程阻塞或死循环，这里简单直接调用
            # 注意：如果日志量巨大，可能需要缓冲或限流
            self.window_service.send_to_frontend("onBackendLog", msg)
        except Exception:
            self.handleError(record)

class ApiService:
    def __init__(self):
        self.api_client = BilibiliApi()
        self.config_manager = Config()
        self.session_state = SessionState()
        
        # Initialize services
        self.window_service = WindowService()
        self.user_service = UserService(self.api_client, self.config_manager, self.session_state)
        self.live_service = LiveService(self.api_client, self.config_manager, self.session_state)
        self.auth_service = AuthService(self.api_client, self.user_service, self.live_service, self.session_state)
        self.danmu_service = DanmuService(self.api_client, self.session_state)

        # 防止并发 start/stop（前端/托盘/定时）
        self._live_lock = threading.Lock()
        
        # 设置弹幕回调
        self.danmu_service.set_callback(self._on_danmu_message)
        # self.danmu_service.set_log_callback(self._on_backend_log) # 不再需要单独的回调，统一走 logging
        
        # 配置日志转发到前端
        self._setup_logging()

        # Initial setup
        self.user_service.init_current_user()

        # 定时开播服务
        self.schedule_service = ScheduleService(
            self.config_manager,
            self.session_state,
            self.live_service,
            self._live_lock,
            interval_sec=30,
        )
        self.schedule_service.start()

        # 对外状态接口
        self.status_api_service = StatusApiService(self.session_state, host="127.0.0.1")
        port = int(self.config_manager.data.get("status_api_port", 0) or 0)
        if port > 0:
            self.status_api_service.start(port)
        
        # Asyncio loop for danmu
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._start_loop, args=(self.loop,), daemon=True)
        self.loop_thread.start()

    def _setup_logging(self):
        """配置日志处理器，将 INFO 及以上级别的日志转发到前端"""
        root_logger = logging.getLogger()
        frontend_handler = FrontendLogHandler(self.window_service)
        frontend_handler.setLevel(logging.INFO) # 只转发 INFO 及以上
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        frontend_handler.setFormatter(formatter)
        root_logger.addHandler(frontend_handler)

    def _start_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _on_danmu_message(self, data):
        """处理弹幕消息回调，推送到前端"""
        # 注意：这里可能在子线程中被调用，webview 的 evaluate_js 应该是线程安全的
        # 前端挂载的函数名为 onDanmuMessage
        self.window_service.send_to_frontend("onDanmuMessage", data)

    # def _on_backend_log(self, msg):
    #     """处理后端日志回调，推送到前端"""
    #     self.window_service.send_to_frontend("onBackendLog", msg)

    # --- Window Proxy Methods ---
    def window_min(self): return self.window_service.window_min()
    def window_max(self): return self.window_service.window_max()
    def window_close(self): 
        # 只有在直播状态下才尝试停止直播
        if self.session_state.is_live:
            self.live_service.stop_live()

        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        try:
            self.schedule_service.stop()
        except Exception:
            pass
        try:
            self.status_api_service.stop()
        except Exception:
            pass
        return self.window_service.window_close(lambda: self.config_manager.save())
    def get_window_position(self): return self.window_service.get_window_position()
    def window_drag(self, target_x, target_y): return self.window_service.window_drag(target_x, target_y)

    # --- User Proxy Methods ---
    def load_saved_config(self): return self.user_service.load_saved_config()
    def refresh_current_user(self): return self.user_service.refresh_current_user()
    def get_account_list(self): return self.user_service.get_account_list()
    def switch_account(self, uid):
        # 切换账户前先停止弹幕，防止新连接使用旧账户
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return self.user_service.switch_account(uid)
    def logout(self, uid):
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return self.user_service.logout(uid)

    # --- Auth Proxy Methods ---
    def get_login_qrcode(self): return self.auth_service.get_login_qrcode()
    def poll_login_status(self, key): return self.auth_service.poll_login_status(key)

    # --- Live Proxy Methods ---
    def get_partitions(self): return self.live_service.get_partitions()
    def update_title(self, title): return self.live_service.update_title(title)
    def update_area(self, p_name, s_name): return self.live_service.update_area(p_name, s_name)
    def start_live(self, p_name=None, s_name=None): 
        with self._live_lock:
            res = self.live_service.start_live(p_name, s_name)
        # if res['code'] == 0:
        #      # 开启直播成功后，连接弹幕
        #      room_id = self.session_state.room_id
        #      if room_id:
        #          asyncio.run_coroutine_threadsafe(self.danmu_service.connect(room_id), self.loop)
        return res
        
    def stop_live(self): 
        with self._live_lock:
            res = self.live_service.stop_live()
        if res and res.get("code") == 0:
            try:
                self.schedule_service.mark_manual_stop()
            except Exception:
                pass
        return res

    # --- Danmu Methods ---
    def start_danmu_monitor(self):
        """开启弹幕监听，如果已在运行则跳过"""
        if not bool(self.config_manager.data.get("danmu_try_fetch", False)):
            return {"code": -1, "msg": "弹幕获取已关闭（请在弹幕页开启开关）"}
        if self.danmu_service.running:
            return {"code": 0, "msg": "弹幕已在运行"}
        room_id = self.session_state.room_id
        if not room_id:
             return {"code": -1, "msg": "未获取到房间ID"}
        asyncio.run_coroutine_threadsafe(self.danmu_service.connect(room_id), self.loop)
        return {"code": 0}

    def stop_danmu_monitor(self):
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return {"code": 0}

    def send_danmu(self, msg):
        """发送弹幕"""
        return self.danmu_service.send_danmu(msg)

    def get_danmu_config(self):
        return {"code": 0, "data": {"try_fetch": bool(self.config_manager.data.get("danmu_try_fetch", False))}}

    def set_danmu_config(self, try_fetch):
        self.config_manager.data["danmu_try_fetch"] = bool(try_fetch)
        self.config_manager.save()
        if not bool(try_fetch):
            asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return {"code": 0}

    # --- App Config Methods ---
    def get_app_config(self):
        import sys
        # 使用实际托盘运行状态（由 main.py 设置）
        has_tray = getattr(self, 'tray_active', False)
        config = {
            "min_to_tray": self.config_manager.data.get("min_to_tray", True),
            "is_win32": sys.platform == 'win32',
            "has_tray": has_tray
        }
        return {"code": 0, "data": config}

    def set_app_config(self, key, value):
        if key == "min_to_tray":
            self.config_manager.data["min_to_tray"] = bool(value)
            self.config_manager.save()
            return {"code": 0}
        return {"code": -1, "msg": "Unknown config key"}

    # --- Status API Methods ---
    def get_status_api_config(self):
        port = int(self.config_manager.data.get("status_api_port", 0) or 0)
        return {
            "code": 0,
            "data": {
                "enabled": port > 0,
                "port": port,
                "url": f"http://127.0.0.1:{port}/status" if port > 0 else "",
            },
        }

    def set_status_api_port(self, port):
        try:
            port = int(port)
        except Exception:
            return {"code": -1, "msg": "端口必须是数字"}
        if port < 0 or port > 65535:
            return {"code": -1, "msg": "端口范围必须是 0-65535"}

        ok, msg = self.status_api_service.start(port)
        if not ok:
            return {"code": -1, "msg": msg}

        self.config_manager.data["status_api_port"] = port
        self.config_manager.save()
        return {"code": 0, "data": {"port": port}}

    # --- Schedule Config Methods ---
    def get_schedule_config(self):
        """获取当前账号的定时开播配置"""
        uid = self.config_manager.data.get("current_uid")
        users = self.config_manager.data.get("users", {})
        if not uid or uid not in users:
            return {"code": -1, "msg": "未登录"}
        schedule = users[uid].get("auto_live_schedule") or {"enabled": False, "periods": []}
        # 兜底补齐字段，避免旧数据缺失
        schedule.setdefault("enabled", False)
        schedule.setdefault("periods", [])
        return {"code": 0, "data": schedule}

    def set_schedule_config(self, enabled, periods):
        """保存当前账号的定时开播配置"""
        uid = self.config_manager.data.get("current_uid")
        users = self.config_manager.data.get("users", {})
        if not uid or uid not in users:
            return {"code": -1, "msg": "未登录"}

        def parse_hhmm(value):
            if not isinstance(value, str):
                raise ValueError("时间格式错误")
            value = value.strip()
            parts = value.split(":")
            if len(parts) != 2:
                raise ValueError("时间格式错误")
            hour = int(parts[0])
            minute = int(parts[1])
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError("时间范围错误")
            return f"{hour:02d}:{minute:02d}"

        if periods is None:
            periods = []
        if not isinstance(periods, list):
            return {"code": -1, "msg": "periods 必须是数组"}
        if len(periods) > 20:
            return {"code": -1, "msg": "最多支持 20 个时段"}

        normalized = []
        try:
            for p in periods:
                if not isinstance(p, dict):
                    raise ValueError("时段格式错误")
                start = parse_hhmm(p.get("start", ""))
                end = parse_hhmm(p.get("end", ""))
                normalized.append({"start": start, "end": end})
        except Exception as e:
            return {"code": -1, "msg": str(e)}

        users[uid]["auto_live_schedule"] = {"enabled": bool(enabled), "periods": normalized}
        self.config_manager.save()
        try:
            self.schedule_service.wakeup()
        except Exception:
            pass
        return {"code": 0}

    def get_version(self):
        """获取应用版本号"""
        import os, sys
        try:
            if getattr(sys, 'frozen', False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            version_file = os.path.join(base, 'VERSION')
            if os.path.exists(version_file):
                with open(version_file, 'r', encoding='utf-8') as f:
                    return {"code": 0, "version": f.read().strip()}
        except Exception:
            pass
        return {"code": 0, "version": "dev"}
