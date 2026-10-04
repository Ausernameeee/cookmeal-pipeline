# cookmeal-pipeline

把 YouTube 上的科普视频，一键做成**中英双语压制版**（"烤肉"）。

```
YouTube URL
  → yt-dlp 下载
  → ffmpeg 抽音频
  → faster-whisper ASR（CPU int8）
  → Gemini 批量翻译
  → ffmpeg 双语字幕压制成片
```

## 为什么有这个项目

之前用 [FineSub](https://github.com/caca2331/finesub) 半手动烤肉，但它的 ASR 核心是 Windows-only 的 CTranslate2 补丁版，Linux 上跑不了。于是按同一套工作流（下载 → 转写 → 翻译 → 双语压制）在 Linux 上重写了一遍，纯 CPU 可跑。

**主要灵感来源：[FineSub](https://github.com/caca2331/finesub)（作者 [@caca2331](https://github.com/caca2331)）** —— 感谢它验证了这条工作流。本仓库代码全部重写，与 FineSub 无代码继承关系。

## 快速开始

依赖：Python 3.10+、ffmpeg、yt-dlp、中文字体（默认字幕样式用 Noto Sans CJK SC，可用 `SUBTITLE_FONT` 环境变量改）。

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="你的 key"   # 免费申请见下
./run_pipeline.sh "https://www.youtube.com/watch?v=xxxx" [name]
# 成片：work/<name>_final.mp4
```

可选环境变量：`PYTHON`（python 解释器路径）、`ASR_MODEL`（默认 `tiny.en`，可换更大的 whisper 模型）、`SUBTITLE_FONT`。

### Gemini 免费 key

去 [Google AI Studio](https://aistudio.google.com) 点 "Get API key"，免费额度做字幕翻译绰绰有余。翻译走 `--model` 可换别的模型（默认 `gemini-3.5-flash-lite`）。

## 核心设计：断句逻辑

小模型（如 tiny.en）在 CPU 上跑，原始分段碎得没法看（半句话一切）。`asr.py` 的 `resegment()` 用词级时间戳重新断句，共四步：

1. **按句末标点 / 长停顿分句**：遇到 `.?!…` 或词间停顿 > 1.2s 就切。
2. **碎片合并**：< 4 个词且 < 2.5s 的碎片往后粘到下一段；但以句末标点结尾的完整短句会被保护起来，不参与合并。
3. **超长拆分**：> 20 词的段按逗号 / 分号 / 冒号拆成从句，时长按字符数比例分配。注意：**只有词数会触发拆分** —— 说得慢不代表要拆。
4. **第二遍收尾**：只收真正的碎片（< 3 词且 < 1s），往**前**粘回它自己的从句，避免把两个句子粘在一起。

这套逻辑是经过三轮实际烤肉反馈打磨出来的。实测：5.5 分钟的 TED-Ed 视频，42 段原始分段 → 67 段可读字幕，全程约 2 分钟（CPU），翻译走免费额度，基本零成本。

## B站自动投稿

`bili_upload.py` 直接调 B站 web API 投稿：preupload 拿上传地址 → upos 分片上传 → `x/vu/web/add` 提交，已实测投稿成功。cookie 从 `BILIBILI_COOKIE_FILE` 环境变量指定的文件读取，文件里写一行：

```
BILIBILI_COOKIE="SESSDATA=...; bili_jct=...; DedeUserID=..."
```

（600 权限，别进仓库。）

```bash
python3 bili_upload.py <mp4> --title "标题" --desc-file desc.txt \
    --tid 124 --tag "tag1,tag2" --source "https://原视频链接"
# 分片传完了但提交失败时，只重提交不重传：
python3 bili_upload.py <mp4> ... --skip-upload <filename>
```

### 踩过的坑（2026-10-05 实测）

- **preupload 参数必须照抄 [biliup](https://github.com/biliup/biliup-rs)**：`r=upos`、`profile=ugcupos/bup`、`ssl=0`、`version=2.11.0`、`build=2110000`，外加 `name`（文件名）和 `size`（字节数）。返回的 auth 签名和这套参数绑定，`profile` 写错就 403/400。
- **分片上传三步走同一个 URL**：`POST {endpoint}/{upos_uri 去掉 upos:// 前缀}?uploads&output=json` 初始化（带 `X-Upos-Auth` 头）→ `PUT` 同一 URL 逐片上传 → `POST` 同一 URL 收尾。分片参数是 camelCase（`uploadId`、`partNumber`、`chunk`、`chunks`、`size`、`start`、`end`、`total`），写成 snake_case 就不认。
- **收尾 body 是 JSON**：`{"parts": [{"partNumber": n, "eTag": "etag"}, ...]}`，`eTag` 实测填任意值可过。
- **提交必须 JSON body**：`POST https://member.bilibili.com/x/vu/web/add?t=<毫秒时间戳>&csrf=<bili_jct>`，用表单编码提交会被打回 `21001 参数错误`。
- **简介有字数限制**：超长会被打回 `21010`，1300 字左右实测可过。
- **默认按"转载"投稿**（`copyright=2`），记得填 `--source` 原视频链接；自制视频把 `copyright` 改成 1。

## Roadmap

- [x] 一键 pipeline：下载 → ASR → 翻译 → 双语压制
- [x] B站自动投稿（`bili_upload.py`：preupload → upos 分片上传 → web API 提交，已跑通）
- [ ] 人声分离 / 说话人区分
- [ ] 时间轴稳定化
- [ ] 术语表（专有名词统一翻译）

## License

MIT
