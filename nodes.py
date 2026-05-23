from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

try:
    import folder_paths
except Exception:  # pragma: no cover - available inside ComfyUI
    folder_paths = None

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover - ComfyUI ships Pillow
    Image = None
    _PIL_IMPORT_ERROR = exc
else:
    _PIL_IMPORT_ERROR = None


DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-2-0-260128"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}


def _env_api_key() -> str:
    return (
        os.environ.get("ARK_API_KEY")
        or os.environ.get("VOLCENGINE_ARK_API_KEY")
        or os.environ.get("VOLCENGINE_API_KEY")
        or ""
    )


def _clean_base_url(base_url: str) -> str:
    base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url:
        return DEFAULT_BASE_URL
    return base_url


def _api_key(api_key: str) -> str:
    key = (api_key or "").strip() or _env_api_key().strip()
    if not key:
        raise RuntimeError(
            "Missing Ark API key. Paste it into api_key or set ARK_API_KEY."
        )
    return key


def _split_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw in (value or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _parse_extra_json(value: str) -> dict[str, Any]:
    value = (value or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"extra_json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("extra_json must be a JSON object.")
    return parsed


def _request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine API HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Volcengine API request failed: {exc}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Volcengine API returned non-JSON response: {body}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Volcengine API returned unexpected response: {body}")
    return parsed


def _create_task(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json(
        "POST",
        f"{_clean_base_url(base_url)}/contents/generations/tasks",
        api_key,
        payload,
    )


def _get_task(base_url: str, api_key: str, task_id: str) -> dict[str, Any]:
    encoded_id = urllib.parse.quote(task_id.strip(), safe="")
    return _request_json(
        "GET",
        f"{_clean_base_url(base_url)}/contents/generations/tasks/{encoded_id}",
        api_key,
    )


def _task_id(response: dict[str, Any]) -> str:
    value = response.get("id") or response.get("task_id")
    if not value:
        raise RuntimeError(f"Could not find task id in response: {response}")
    return str(value)


def _output_dir() -> Path:
    if folder_paths is not None:
        root = Path(folder_paths.get_output_directory())
    else:
        root = Path(__file__).resolve().parent / "output"
    path = root / "seedance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_task_name(task_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", task_id).strip("._") or "task"


def _url_extension(url: str, default: str = ".mp4") -> str:
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"}:
        return ext
    mime, _ = mimetypes.guess_type(path)
    if mime:
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return guessed
    return default


def _download(url: str, task_id: str, default_ext: str = ".mp4") -> str:
    if not url:
        return ""
    ext = _url_extension(url, default_ext)
    filename = f"seedance_{_safe_task_name(task_id)}_{int(time.time())}{ext}"
    target = _output_dir() / filename

    request = urllib.request.Request(url)
    request.add_header("User-Agent", "ComfyUI-Volcengine-Seedance/1.0")
    with urllib.request.urlopen(request, timeout=300) as response:
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    return str(target)


def _nested_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "href"):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested
    return ""


def _extract_video_url(response: dict[str, Any]) -> str:
    content = response.get("content") or response.get("output") or {}
    if isinstance(content, dict):
        for key in ("video_url", "file_url", "url"):
            url = _nested_url(content.get(key))
            if url:
                return url
    return _nested_url(response.get("video_url") or response.get("file_url"))


def _extract_last_frame_url(response: dict[str, Any]) -> str:
    content = response.get("content") or response.get("output") or {}
    if isinstance(content, dict):
        for key in ("last_frame_url", "image_url"):
            url = _nested_url(content.get(key))
            if url:
                return url
    return _nested_url(response.get("last_frame_url"))


def _to_pil_image(image: Any) -> Image.Image:
    if Image is None:
        raise RuntimeError(f"Pillow import failed: {_PIL_IMPORT_ERROR}")

    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("NumPy is required to encode IMAGE inputs.") from exc

    if hasattr(image, "detach"):
        image = image.detach().cpu().numpy()
    array = np.asarray(image)

    if array.ndim == 4:
        array = array[0]
    if array.ndim == 3 and array.shape[0] in (1, 3, 4) and array.shape[-1] not in (1, 3, 4):
        array = np.moveaxis(array, 0, -1)
    if array.ndim == 2:
        pass
    elif array.ndim == 3 and array.shape[-1] in (1, 3, 4):
        pass
    else:
        raise RuntimeError(f"Unsupported IMAGE shape for Seedance input: {array.shape}")

    if array.dtype.kind == "f":
        array = np.clip(array, 0.0, 1.0) * 255.0
    else:
        array = np.clip(array, 0, 255)
    array = array.astype(np.uint8)

    pil = Image.fromarray(array)
    if pil.mode not in {"RGB", "RGBA", "L"}:
        pil = pil.convert("RGB")
    return pil


def _image_to_data_uri(image: Any, image_format: str = "PNG") -> str:
    pil = _to_pil_image(image)
    buffer = BytesIO()
    fmt = image_format.upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt == "JPEG" and pil.mode == "RGBA":
        pil = pil.convert("RGB")
    pil.save(buffer, format=fmt)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    mime = "jpeg" if fmt == "JPEG" else fmt.lower()
    return f"data:image/{mime};base64,{encoded}"


def _media_item(kind: str, url: str, role: str | None = None) -> dict[str, Any]:
    if kind == "image":
        item: dict[str, Any] = {"type": "image_url", "image_url": {"url": url}}
    elif kind == "video":
        item = {"type": "video_url", "video_url": {"url": url}}
    elif kind == "audio":
        item = {"type": "audio_url", "audio_url": {"url": url}}
    else:
        raise ValueError(f"Unsupported media kind: {kind}")
    if role:
        item["role"] = role
    return item


def _build_content(
    prompt: str,
    image_role: str,
    first_frame_url: str,
    last_frame_url: str,
    reference_image_urls: str,
    reference_video_urls: str,
    reference_audio_urls: str,
    image: Any | None = None,
    last_frame_image: Any | None = None,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    prompt = (prompt or "").strip()
    if prompt:
        content.append({"type": "text", "text": prompt})

    first_frame_count = 0
    last_frame_count = 0
    reference_count = 0

    first_frame_url = (first_frame_url or "").strip()
    if first_frame_url:
        content.append(_media_item("image", first_frame_url, "first_frame"))
        first_frame_count += 1

    if image is not None and image_role != "none":
        role = "first_frame" if image_role == "first_frame" else "reference_image"
        content.append(_media_item("image", _image_to_data_uri(image), role))
        if role == "first_frame":
            first_frame_count += 1
        else:
            reference_count += 1

    last_frame_url = (last_frame_url or "").strip()
    if last_frame_url:
        content.append(_media_item("image", last_frame_url, "last_frame"))
        last_frame_count += 1

    if last_frame_image is not None:
        content.append(_media_item("image", _image_to_data_uri(last_frame_image), "last_frame"))
        last_frame_count += 1

    for url in _split_lines(reference_image_urls):
        content.append(_media_item("image", url, "reference_image"))
        reference_count += 1

    for url in _split_lines(reference_video_urls):
        content.append(_media_item("video", url, "reference_video"))
        reference_count += 1

    for url in _split_lines(reference_audio_urls):
        content.append(_media_item("audio", url, "reference_audio"))
        reference_count += 1

    if not content:
        raise RuntimeError("Seedance content is empty. Provide prompt or media input.")
    if first_frame_count > 1:
        raise RuntimeError("Only one first frame is allowed. Use either image or first_frame_url.")
    if last_frame_count > 1:
        raise RuntimeError("Only one last frame is allowed. Use either last_frame_image or last_frame_url.")
    if last_frame_count and first_frame_count != 1:
        raise RuntimeError("A last frame requires exactly one first frame.")
    if (first_frame_count or last_frame_count) and reference_count:
        raise RuntimeError(
            "First/last frame mode cannot be mixed with reference image, video, or audio. "
            "For multimodal reference, set image_role to reference_image and describe the "
            "intended first/last frame in the prompt."
        )

    return content


def _build_payload(
    model: str,
    prompt: str,
    image_role: str,
    first_frame_url: str,
    last_frame_url: str,
    reference_image_urls: str,
    reference_video_urls: str,
    reference_audio_urls: str,
    resolution: str,
    ratio: str,
    duration: int,
    seed: int,
    generate_audio: bool,
    watermark: bool,
    return_last_frame: bool,
    execution_expires_after: int,
    priority: int,
    safety_identifier: str,
    callback_url: str,
    extra_json: str,
    image: Any | None = None,
    last_frame_image: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "content": _build_content(
            prompt=prompt,
            image_role=image_role,
            first_frame_url=first_frame_url,
            last_frame_url=last_frame_url,
            reference_image_urls=reference_image_urls,
            reference_video_urls=reference_video_urls,
            reference_audio_urls=reference_audio_urls,
            image=image,
            last_frame_image=last_frame_image,
        ),
        "resolution": resolution,
        "ratio": ratio,
        "duration": int(duration),
        "seed": int(seed),
        "generate_audio": bool(generate_audio),
        "watermark": bool(watermark),
        "return_last_frame": bool(return_last_frame),
        "execution_expires_after": int(execution_expires_after),
    }

    if int(priority) > 0:
        payload["priority"] = int(priority)
    if (safety_identifier or "").strip():
        payload["safety_identifier"] = safety_identifier.strip()
    if (callback_url or "").strip():
        payload["callback_url"] = callback_url.strip()

    payload.update(_parse_extra_json(extra_json))
    return payload


def _summary(
    task_id: str,
    status: str,
    video_path: str,
    video_url: str,
    last_frame_path: str = "",
    last_frame_url: str = "",
) -> list[str]:
    lines = [f"task_id: {task_id}", f"status: {status}"]
    if video_path:
        lines.append(f"video_path: {video_path}")
    if video_url:
        lines.append(f"video_url: {video_url}")
    if last_frame_path:
        lines.append(f"last_frame_path: {last_frame_path}")
    if last_frame_url:
        lines.append(f"last_frame_url: {last_frame_url}")
    return lines


class SeedanceGenerateNode:
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "video_path",
        "video_url",
        "last_frame_path",
        "last_frame_url",
        "task_id",
        "status",
        "response_json",
    )
    FUNCTION = "generate"
    CATEGORY = "Volcengine/Seedance"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "model": ("STRING", {"default": DEFAULT_MODEL, "multiline": False}),
                "resolution": (["720p", "480p", "1080p"], {"default": "720p"}),
                "ratio": (
                    ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
                    {"default": "adaptive"},
                ),
                "duration": ("INT", {"default": 5, "min": -1, "max": 15, "step": 1}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 4294967295, "step": 1}),
                "generate_audio": ("BOOLEAN", {"default": True}),
                "watermark": ("BOOLEAN", {"default": False}),
                "return_last_frame": ("BOOLEAN", {"default": False}),
                "wait_for_result": ("BOOLEAN", {"default": True}),
                "download_video": ("BOOLEAN", {"default": True}),
                "download_last_frame": ("BOOLEAN", {"default": False}),
                "poll_interval": ("INT", {"default": 30, "min": 5, "max": 120, "step": 1}),
                "timeout_seconds": ("INT", {"default": 1800, "min": 60, "max": 7200, "step": 30}),
                "image_role": (["first_frame", "reference_image", "none"], {"default": "first_frame"}),
                "first_frame_url": ("STRING", {"default": "", "multiline": False}),
                "last_frame_url": ("STRING", {"default": "", "multiline": False}),
                "reference_image_urls": ("STRING", {"default": "", "multiline": True}),
                "reference_video_urls": ("STRING", {"default": "", "multiline": True}),
                "reference_audio_urls": ("STRING", {"default": "", "multiline": True}),
                "execution_expires_after": (
                    "INT",
                    {"default": 172800, "min": 3600, "max": 259200, "step": 60},
                ),
                "priority": ("INT", {"default": 0, "min": 0, "max": 9, "step": 1}),
                "safety_identifier": ("STRING", {"default": "", "multiline": False}),
                "callback_url": ("STRING", {"default": "", "multiline": False}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL, "multiline": False}),
                "extra_json": ("STRING", {"default": "{}", "multiline": True}),
            },
            "optional": {
                "image": ("IMAGE",),
                "last_frame_image": ("IMAGE",),
            },
        }

    def generate(self, **kwargs: Any) -> dict[str, Any]:
        key = _api_key(kwargs.get("api_key", ""))
        payload = _build_payload(
            model=kwargs.get("model", DEFAULT_MODEL),
            prompt=kwargs.get("prompt", ""),
            image_role=kwargs.get("image_role", "first_frame"),
            first_frame_url=kwargs.get("first_frame_url", ""),
            last_frame_url=kwargs.get("last_frame_url", ""),
            reference_image_urls=kwargs.get("reference_image_urls", ""),
            reference_video_urls=kwargs.get("reference_video_urls", ""),
            reference_audio_urls=kwargs.get("reference_audio_urls", ""),
            resolution=kwargs.get("resolution", "720p"),
            ratio=kwargs.get("ratio", "adaptive"),
            duration=kwargs.get("duration", 5),
            seed=kwargs.get("seed", -1),
            generate_audio=kwargs.get("generate_audio", True),
            watermark=kwargs.get("watermark", False),
            return_last_frame=kwargs.get("return_last_frame", False),
            execution_expires_after=kwargs.get("execution_expires_after", 172800),
            priority=kwargs.get("priority", 0),
            safety_identifier=kwargs.get("safety_identifier", ""),
            callback_url=kwargs.get("callback_url", ""),
            extra_json=kwargs.get("extra_json", "{}"),
            image=kwargs.get("image"),
            last_frame_image=kwargs.get("last_frame_image"),
        )

        base_url = kwargs.get("base_url", DEFAULT_BASE_URL)
        create_response = _create_task(base_url, key, payload)
        task_id = _task_id(create_response)

        response = create_response
        status = str(response.get("status") or "submitted")
        video_url = ""
        video_path = ""
        last_frame_url = ""
        last_frame_path = ""

        if bool(kwargs.get("wait_for_result", True)):
            deadline = time.time() + int(kwargs.get("timeout_seconds", 1800))
            interval = int(kwargs.get("poll_interval", 15))
            while True:
                response = _get_task(base_url, key, task_id)
                status = str(response.get("status") or "")
                if status in TERMINAL_STATUSES:
                    break
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"Timed out waiting for Seedance task {task_id}; latest status: {status}"
                    )
                time.sleep(interval)

            if status != "succeeded":
                error = response.get("error")
                raise RuntimeError(
                    f"Seedance task {task_id} ended with status {status}: {error}"
                )

            video_url = _extract_video_url(response)
            last_frame_url = _extract_last_frame_url(response)
            if bool(kwargs.get("download_video", True)) and video_url:
                video_path = _download(video_url, task_id)
            if bool(kwargs.get("download_last_frame", False)) and last_frame_url:
                last_frame_path = _download(last_frame_url, f"{task_id}_last_frame", ".png")

        response_json = json.dumps(response, ensure_ascii=False, indent=2)
        result = (
            video_path,
            video_url,
            last_frame_path,
            last_frame_url,
            task_id,
            status,
            response_json,
        )
        return {
            "ui": {
                "text": _summary(
                    task_id, status, video_path, video_url, last_frame_path, last_frame_url
                )
            },
            "result": result,
        }


class SeedanceQueryNode:
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "video_path",
        "video_url",
        "last_frame_path",
        "last_frame_url",
        "task_id",
        "status",
        "response_json",
    )
    FUNCTION = "query"
    CATEGORY = "Volcengine/Seedance"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "task_id": ("STRING", {"default": "", "multiline": False}),
                "download_video": ("BOOLEAN", {"default": True}),
                "download_last_frame": ("BOOLEAN", {"default": False}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL, "multiline": False}),
            }
        }

    def query(self, **kwargs: Any) -> dict[str, Any]:
        key = _api_key(kwargs.get("api_key", ""))
        task_id = str(kwargs.get("task_id", "")).strip()
        if not task_id:
            raise RuntimeError("task_id is required.")

        response = _get_task(kwargs.get("base_url", DEFAULT_BASE_URL), key, task_id)
        status = str(response.get("status") or "")
        video_url = _extract_video_url(response)
        last_frame_url = _extract_last_frame_url(response)
        video_path = ""
        last_frame_path = ""

        if status == "succeeded":
            if bool(kwargs.get("download_video", True)) and video_url:
                video_path = _download(video_url, task_id)
            if bool(kwargs.get("download_last_frame", False)) and last_frame_url:
                last_frame_path = _download(last_frame_url, f"{task_id}_last_frame", ".png")

        response_with_downloads = dict(response)
        if last_frame_path:
            response_with_downloads["last_frame_path"] = last_frame_path
        response_json = json.dumps(response_with_downloads, ensure_ascii=False, indent=2)
        result = (
            video_path,
            video_url,
            last_frame_path,
            last_frame_url,
            task_id,
            status,
            response_json,
        )
        return {
            "ui": {
                "text": _summary(
                    task_id, status, video_path, video_url, last_frame_path, last_frame_url
                )
            },
            "result": result,
        }


NODE_CLASS_MAPPINGS = {
    "VolcengineSeedanceGenerate": SeedanceGenerateNode,
    "VolcengineSeedanceQuery": SeedanceQueryNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VolcengineSeedanceGenerate": "Seedance 2.0 Generate (Volcengine)",
    "VolcengineSeedanceQuery": "Seedance 2.0 Query Task (Volcengine)",
}
