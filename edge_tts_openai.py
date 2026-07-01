import os
import tempfile
from typing import Union
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import edge_tts
import io

from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip
import requests
from pydantic import BaseModel

class MakeVideoRequest(BaseModel):
    image_url: str = ""
    audio_url: str = ""
    srt: str = ""
    width: int = 1080
    height: int = 1920
    fontsize: int = 36

app = FastAPI()

# ============ 配置 ============
OUTPUT_DIR = os.path.expanduser("~/dify_videos")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"

# ============ 工具函数 ============

def parse_srt(srt_text: str):
    """解析 SRT → [(start, end, text), ...]"""
    entries = []
    for block in srt_text.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            try:
                s, e = lines[1].split(" --> ")
                text = " ".join(lines[2:])
                def ts(t):
                    h, m, s_ = t.replace(",", ".").split(":")
                    return int(h) * 3600 + int(m) * 60 + float(s_)
                entries.append((ts(s), ts(e), text))
            except:
                pass
    return entries


import requests

def ensure_full_url(url: str) -> str:
    if url.startswith("/files/"):
        return "http://127.0.0.1:5001" + url
    return url

def download(url, path, timeout=30):
    url = ensure_full_url(url)

    proxies = {
        "http": None,
        "https": None,
    }
    r = requests.get(url, timeout=timeout, proxies=proxies)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return len(r.content)


# ============ TTS 接口（你原有的） ============

@app.post("/v1/audio/speech")
async def tts(req: Request):
    body = await req.json()
    text = body.get("input", "")
    voice = body.get("voice", "zh-CN-XiaoxiaoNeural")
    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return Response(content=buf.getvalue(), media_type="audio/mpeg")

@app.post("/make_video")
async def make_video(req: MakeVideoRequest):
    print(req)
    try:
        work_dir = tempfile.mkdtemp()
        print(f"[INFO] work_dir={work_dir}")

        # 1️⃣ 下载背景图
        img_path = os.path.join(work_dir, "bg.jpg")
        size = download(req.image_url, img_path)
        print(f"[OK] image: {size} bytes")

        # 2️⃣ 获取音频（优先 audio_url，否则用 TTS）
        audio_path = os.path.join(work_dir, "audio.mp3")

        if req.audio_url:
            size = download(req.audio_url, audio_path)
            print(f"[OK] audio from URL: {size} bytes")
        elif req.text:
            print(f"[INFO] TTS text length: {len(req.text)}")
            communicate = edge_tts.Communicate(req.text, req.voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            with open(audio_path, "wb") as f:
                f.write(buf.getvalue())
            print(f"[OK] TTS audio: {len(buf.getvalue())} bytes")
        else:
            raise HTTPException(400, "必须提供 audio_url 或 text")

        # 3️⃣ 加载音频
        audio = AudioFileClip(audio_path)
        duration = audio.duration
        print(f"[INFO] duration={duration:.2f}s")

        # 4️⃣ 创建视频
        # img_clip = ImageClip(img_path, duration=duration).resize((req.width, req.height))
        img_clip = (
            ImageClip(img_path, duration=duration)
            .resized((req.width, req.height))
        )

        # 5️⃣ 字幕
        subtitle_clips = []
        if req.srt:
            for start, end, text in parse_srt(req.srt):
                subtitle_clips.append(
                    TextClip(
                        text,
                        font_size=req.fontsize,
                        color="white",
                        stroke_color="black",
                        stroke_width=2,
                        font=FONT_PATH,
                        size=(req.width - 120, None),
                        method="caption",
                        horizontal_align="center",
                        vertical_align="center",
                    )
                    .set_start(start)
                    .set_duration(end - start)
                    .set_position(("center", req.height - 320))
                )

        video = CompositeVideoClip([img_clip] + subtitle_clips).set_audio(audio)

        # 6️⃣ 输出
        filename = f"news_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(OUTPUT_DIR, filename)

        video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
        )

        # 清理
        video.close()
        audio.close()
        img_clip.close()

        print(f"[OK] video saved: {output_path}")

        return {
            "success": True,
            "video_path": output_path,
            "video_url": f"file://{output_path}",
            "duration": round(duration, 2),
            "filename": filename,
        }

    except Exception as e:
        import traceback
        print(f"[ERROR]\n{traceback.format_exc()}")
        raise HTTPException(500, detail=str(e))


# ============ 健康检查 ============

@app.get("/health")
def health():
    return {"status": "ok", "output_dir": OUTPUT_DIR}
