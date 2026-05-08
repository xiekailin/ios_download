from __future__ import annotations


def extract_ytdlp_error_text(stdout: str = "", stderr: str = "") -> str:
    lines = [line.strip() for line in f"{stderr}\n{stdout}".splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line.removeprefix("ERROR:").strip()
    return lines[-1] if lines else "yt-dlp failed"


def is_ytdlp_login_required(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(
        marker in lowered
        for marker in (
            "sign in",
            "login",
            "cookies",
            "not a bot",
            "confirm your age",
            "inappropriate for some users",
        )
    )


def ytdlp_safe_error_text(error_text: str) -> str:
    if is_ytdlp_login_required(error_text):
        return "yt-dlp login verification required"
    return error_text


def ytdlp_user_message(error_text: str, *, retried_with_cookie: bool = False) -> str:
    lowered = error_text.lower()
    if is_ytdlp_login_required(error_text):
        if retried_with_cookie:
            return "该平台需要登录验证。已自动尝试使用已配置的登录 Cookie，请确认 Cookie 有效后重试。"
        return "该平台需要登录验证。请在 Mac 端上传已登录平台的 Cookie 后重试。"
    if "requested format" in lowered or "format is not available" in lowered or "no video formats" in lowered:
        return "当前视频格式不可用，请稍后重试。"
    if "private" in lowered or "unavailable" in lowered or "has been removed" in lowered or "made this video available" in lowered or ("not available" in lowered and "format" not in lowered):
        return "该视频不可访问，可能已被删除、设为私密或需要权限。"
    if "timed out" in lowered or "timeout" in lowered:
        return "网络超时，请稍后重试。"
    return "下载视频失败，请稍后重试。"
