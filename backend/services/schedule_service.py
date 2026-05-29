import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger("ScheduleService")


class ScheduleService:
    def __init__(self, config_manager, session_state, live_service, live_lock, interval_sec=30):
        self.config = config_manager
        self.state = session_state
        self.live_service = live_service
        self.live_lock = live_lock
        self.interval_sec = max(5, int(interval_sec))

        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread = None

        # 避免人脸认证/失败时频繁重试
        self._start_block_until = 0.0

        # 如果在自动开播时段内手动停播，则当前时段内不再自动开播
        self._manual_stop_suppress = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wakeup_event.clear()
        self._thread = threading.Thread(target=self._run, name="ScheduleService", daemon=True)
        self._thread.start()
        logger.info("Schedule service started")

    def stop(self):
        self._stop_event.set()
        self._wakeup_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Schedule service stopped")

    def wakeup(self):
        self._wakeup_event.set()

    def mark_manual_stop(self):
        """
        在“自动开播时段内”手动停播后，抑制当前时段的自动开播。
        该抑制会在离开所有开播时段（should_live=False）后自动解除。
        """
        try:
            user, schedule = self._get_current_schedule()
            if not user or not schedule or not schedule.get("enabled"):
                return
            periods = schedule.get("periods") or []
            if not periods:
                return

            now = datetime.now()
            now_min = now.hour * 60 + now.minute

            should_live = False
            for p in periods:
                if not isinstance(p, dict):
                    continue
                start_min = self._parse_hhmm_to_minutes(p.get("start"))
                end_min = self._parse_hhmm_to_minutes(p.get("end"))
                if self._is_now_in_period(now_min, start_min, end_min):
                    should_live = True
                    break

            if should_live:
                self._manual_stop_suppress = True
                logger.info("Manual stop detected during schedule period; suppress auto start until period ends")
                self.wakeup()
        except Exception:
            logger.exception("Failed to mark manual stop")

    def _parse_hhmm_to_minutes(self, value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        parts = value.split(":")
        if len(parts) != 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except Exception:
            return None
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour * 60 + minute

    def _is_now_in_period(self, now_min, start_min, end_min):
        if start_min is None or end_min is None:
            return False
        if start_min == end_min:
            return False
        # 普通区间：start <= now < end
        if start_min < end_min:
            return start_min <= now_min < end_min
        # 跨天区间：23:00-01:00 => now>=23:00 or now<01:00
        return now_min >= start_min or now_min < end_min

    def _get_current_schedule(self):
        uid = self.config.data.get("current_uid")
        users = self.config.data.get("users", {})
        if not uid or uid not in users:
            return None, None
        user = users[uid]
        schedule = user.get("auto_live_schedule") or {}
        enabled = bool(schedule.get("enabled", False))
        periods = schedule.get("periods", [])
        if not isinstance(periods, list):
            periods = []
        return user, {"enabled": enabled, "periods": periods}

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Schedule tick failed")

            # 被 wakeup() 触发则立刻下一轮；否则按 interval 运行
            self._wakeup_event.wait(self.interval_sec)
            self._wakeup_event.clear()

    def _tick(self):
        user, schedule = self._get_current_schedule()
        if not user or not schedule or not schedule.get("enabled"):
            return
        periods = schedule.get("periods") or []
        if not periods:
            return
        # 未登录/未初始化不做任何操作
        if not self.state.room_id or not self.state.csrf:
            return

        now = datetime.now()
        now_min = now.hour * 60 + now.minute

        should_live = False
        for p in periods:
            if not isinstance(p, dict):
                continue
            start_min = self._parse_hhmm_to_minutes(p.get("start"))
            end_min = self._parse_hhmm_to_minutes(p.get("end"))
            if self._is_now_in_period(now_min, start_min, end_min):
                should_live = True
                break

        # 一旦离开所有开播时段，解除“手动停播抑制”
        if not should_live and self._manual_stop_suppress:
            self._manual_stop_suppress = False

        # 开播
        if should_live and not self.state.is_live:
            if self._manual_stop_suppress:
                return
            if time.time() < self._start_block_until:
                return
            with self.live_lock:
                if self.state.is_live:
                    return
                last_area_name = user.get("last_area_name") or []
                if isinstance(last_area_name, list) and len(last_area_name) >= 2:
                    res = self.live_service.start_live(last_area_name[0], last_area_name[1])
                else:
                    res = self.live_service.start_live()

            if res and res.get("code") == 0:
                logger.info("Auto schedule started live")
            else:
                code = res.get("code") if isinstance(res, dict) else None
                logger.warning(f"Auto schedule start failed: {res}")
                if code in (60024, 60043):
                    # 人脸认证：暂停一段时间避免刷屏
                    self._start_block_until = time.time() + 10 * 60
                else:
                    # 普通失败：短暂退避
                    self._start_block_until = time.time() + 60
            return

        # 停播
        if (not should_live) and self.state.is_live:
            with self.live_lock:
                if not self.state.is_live:
                    return
                res = self.live_service.stop_live()
            if res and res.get("code") == 0:
                logger.info("Auto schedule stopped live")
            else:
                logger.warning(f"Auto schedule stop failed: {res}")
