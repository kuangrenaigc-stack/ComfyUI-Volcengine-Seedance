# ComfyUI Volcengine Seedance

火山方舟 / 火山引擎 Seedance 2.0 视频生成 API 的 ComfyUI 自定义节点。

## 安装

把本目录放到 ComfyUI 的 `custom_nodes` 下：

```powershell
ComfyUI/custom_nodes/ComfyUI-Volcengine-Seedance
```

然后重启 ComfyUI，在节点菜单里找：

```text
Volcengine/Seedance
```

建议把 API Key 放到环境变量，避免写进工作流：

```powershell
$env:ARK_API_KEY="你的火山方舟 API Key"
```

也可以直接填到节点的 `api_key` 输入框。

## 节点

### Seedance 2.0 Generate (Volcengine)

提交生成任务，可选择等待任务完成并自动下载视频。

输出：

- `video_path`：下载到本机的 mp4 路径，默认在 ComfyUI `output/seedance`。
- `video_url`：火山方舟返回的视频 URL。
- `last_frame_path` / `last_frame_url`：开启 `return_last_frame` 后的尾帧路径/URL。
- `task_id`：视频生成任务 ID。
- `status`：任务状态。
- `response_json`：完整响应，便于排查。

常用用法：

- 文生视频：只填 `prompt`。
- 首帧图生视频：连接 ComfyUI 的 `IMAGE` 到 `image`，保持 `image_role=first_frame`。
- 首尾帧：连接 `image` 作为首帧，再连接 `last_frame_image`。
- 公网素材：填 `first_frame_url`、`last_frame_url`、`reference_image_urls`、`reference_video_urls` 或 `reference_audio_urls`。
- 多模态参考：在 `reference_image_urls` / `reference_video_urls` / `reference_audio_urls` 中每行填一个 URL、`asset://...` 素材 ID，或官方支持的 base64 data URI。

官方 quickstart 包的 `python/demo_standard.py` 是一个“参考图 + 参考视频”的视频编辑任务，对应节点参数如下：

```text
prompt: 将视频1礼盒中的香水替换成图片1中的面霜，运镜不变
model: doubao-seedance-2-0-260128
image_role: none
reference_image_urls: https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg
reference_video_urls: https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_edit_video1.mp4
generate_audio: true
ratio: 16:9
duration: 5
watermark: true
poll_interval: 30
```

如果你用 ComfyUI 里的图片输入替代官方 URL，把 `image` 接到节点上，并把 `image_role` 改成 `reference_image`。

### Seedance 2.0 Query Task (Volcengine)

用已有 `task_id` 查询任务，成功时可下载视频和尾帧。

## 注意

- `image` 和 `last_frame_image` 会被转换成 `data:image/png;base64,...` 传给 API；大图会增加请求体大小。
- 官方对不同场景的 `role` 有互斥规则：首帧/首尾帧、多模态参考不要混用。节点会拦截明显冲突，例如同时传两个首帧，或把首尾帧和参考素材混用。
- `wait_for_result=True` 会阻塞当前 ComfyUI 执行队列，直到任务成功、失败或超时。想异步提交时可关闭它，然后用 Query 节点取结果。
- `extra_json` 会合并到最终 request body，可用来加入官方新增字段；如果里面包含 `content`，会覆盖节点自动构建的内容。
