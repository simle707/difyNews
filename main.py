import os
import tempfile
import uuid
from datetime import date
import io
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import edge_tts
import requests

from moviepy import AudioFileClip, CompositeVideoClip, VideoFileClip, TextClip, concatenate_audioclips
import moviepy.video.fx as loop

from models import MakeVideoRequest

app = FastAPI()

OUTPUT_DIR = os.path.expanduser("~/dify_videos")
os.makedirs(OUTPUT_DIR, exist_ok=True)

VIDEO_LIBRARY_DIR = os.path.expanduser("~/studyspace/difyNews/video_library")
os.makedirs(VIDEO_LIBRARY_DIR, exist_ok=True)

FONT_PATH = "/System/Library/Fonts/STHeiti Light.ttc"

app.mount(
    "/static_videos", 
    StaticFiles(directory=VIDEO_LIBRARY_DIR), 
    name="static_videos"
)


# ============ 工具函数 ============
def get_date_elements():
    """彻底自动化：全自动计算干支纪年 + 自动匹配当前年份的农历月日"""
    from datetime import datetime, date
    now = datetime.now()
    cur_year = now.year

    # 1️⃣ 自动计算干支纪年（如：2026 -> 丙午年）
    tiangan = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
    dizhi = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    
    # 公历年份对应的六十甲子索引（以公元4年甲子年为基准）
    idx = (cur_year - 4) % 60
    gz_year = f"{tiangan[idx % 10]}{dizhi[idx % 12]}年"

    # 2️⃣ 英文月份与双位公历日期
    months_en = ["January", "February", "March", "April", "May", "June", 
                 "July", "August", "September", "October", "November", "December"]
    month_str = months_en[now.month - 1]
    day_str = f"{now.day:02d}"

    # 3️⃣ 自动化农历月日扩展数据库（支持 2025, 2026, 2027 年，满足近几年自动切换）
    lunar_db = {
        2025: [
            (date(2025, 1, 29), "正月"), (date(2025, 2, 28), "二月"), (date(2025, 3, 29), "三月"),
            (date(2025, 4, 28), "四月"), (date(2025, 5, 27), "五月"), (date(2025, 6, 25), "六月"),
            (date(2025, 7, 25), "七月"), (date(2025, 8, 23), "八月"), (date(2025, 9, 22), "九月"),
            (date(2025, 10, 21), "十月"), (date(2025, 11, 20), "十一月"), (date(2025, 12, 20), "十二月")
        ],
        2026: [
            (date(2026, 2, 17), "正月"), (date(2026, 3, 19), "二月"), (date(2026, 4, 17), "三月"),
            (date(2026, 5, 16), "四月"), (date(2026, 6, 15), "五月"), (date(2026, 7, 14), "六月"),
            (date(2026, 8, 12), "七月"), (date(2026, 9, 11), "八月"), (date(2026, 10, 11), "九月"),
            (date(2026, 11, 9), "十月"), (date(2026, 12, 9), "十一月"), (date(2027, 1, 8), "十二月")
        ],
        2027: [
            (date(2027, 2, 6), "正月"), (date(2027, 3, 8), "二月"), (date(2027, 4, 7), "三月"),
            (date(2027, 5, 6), "四月"), (date(2027, 6, 5), "五月"), (date(2027, 7, 4), "六月"),
            (date(2027, 7, 3), "闰五月"), # 2027年包含闰五月特殊处理
            (date(2027, 8, 2), "六月"), (date(2027, 8, 31), "七月"), (date(2027, 9, 30), "八月"),
            (date(2027, 10, 29), "九月"), (date(2027, 11, 28), "十月"), (date(2027, 12, 27), "十一月")
        ]
    }

    # 4️⃣ 动态匹配当年农历首日数据
    today_dt = now.date()
    # 如果当前公历是一月且还没过当年的农历春节，需归属到前一年的农历数据计算
    target_year = cur_year
    if cur_year in lunar_db and today_dt < lunar_db[cur_year][0][0]:
        target_year = cur_year - 1
        # 重新计算前一年的干支年
        idx = (target_year - 4) % 60
        gz_year = f"{tiangan[idx % 10]}{dizhi[idx % 12]}年"

    lunar_months = lunar_db.get(target_year, lunar_db[2026]) # 搜不到则保底用2026
    
    matched_month = "正月"
    days_diff = 1
    
    for i in range(len(lunar_months) - 1, -1, -1):
        if today_dt >= lunar_months[i][0]:
            matched_month = lunar_months[i][1]
            days_diff = (today_dt - lunar_months[i][0]).days + 1
            break
            
    lunar_days = ["", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                  "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                  "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
    
    day_lunar_str = lunar_days[days_diff] if days_diff < len(lunar_days) else f"初{days_diff}"
    lunar_str = f"{gz_year} {matched_month}{day_lunar_str}"
    
    return month_str, day_str, lunar_str

def parse_srt(srt_text: str):
    """解析 SRT → [(start, end, text), ...]"""
    entries = []
    if not srt_text:
        return entries
    
    srt_text = re.sub(r'```[a-zA-Z]*\n', '', srt_text)
    srt_text = srt_text.replace('```', '')
    srt_text = srt_text.replace("\r\n", "\n")

    for block in srt_text.strip().split("\n\n"):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        time_line = ""
        text_start_idx = 0
        for idx, line in enumerate(lines):
            if " --> " in line:
                time_line = line
                text_start_idx = idx + 1
                break

        if not time_line:
            continue

        try:
            s, e = time_line.split(" --> ")
            text = " ".join(lines[text_start_idx:])

            def ts(t):
                t = t.replace(",", ".")
                parts = t.split(":")
                if len(parts) == 3:
                    h, m, s_ = parts
                    return int(h) * 3600 + int(m) * 60 + float(s_)
                elif len(parts) == 2:
                    m, s_ = parts
                    return int(m) * 60 + float(s_)
                return 0.0

            if text:
                entries.append((ts(s), ts(e), text))
        except:
            pass
    return entries

def download(url: str, save_path: str):
    """下载资源到指定路径"""
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise HTTPException(400, f"下载失败, 状态码: {response.status_code}")
    with open(save_path, "wb") as f:
        f.write(response.content)
    return len(response.content)


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
    print(f"[INFO] 收到早安视频合成任务...")
    work_dir = None
    bg_video_path = ""
    try:
        work_dir = tempfile.mkdtemp()
        
        if os.path.isdir(VIDEO_LIBRARY_DIR):
            videos = [
                os.path.join(VIDEO_LIBRARY_DIR, f) 
                for f in os.listdir(VIDEO_LIBRARY_DIR)
                if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv"))
            ]
            if not videos:
                raise HTTPException(500, "视频素材库为空")
            bg_video_path = bg_video_path = videos[0]
            print(f"[FAST] 🎯 命中本地素材库！直接读取: {bg_video_path}")
        else:
            raise HTTPException(500, f"VIDEO_LIBRARY_DIR 不存在或不是目录: {VIDEO_LIBRARY_DIR}")

        audio_path = os.path.join(work_dir, "audio.mp3")
        final_srt = req.text

        if req.text:
            print(f"[INFO] TTS text length: {len(req.text)}")
            raw_sentences = re.split(r'([，、。！？；：，,.\!?;\s\n]+)', req.text)
            sentences = ["".join(i).strip() for i in zip(raw_sentences[0::2], raw_sentences[1::2] + [""])]
            sentences = [s for s in sentences if s] # 过滤空串

            audio_clips = []
            srt_blocks = []
            srt_idx = 1
            current_time = 0.0

            def format_time(seconds):
                ms = int((seconds % 1) * 1000)
                s = int(seconds)
                m, s = divmod(s, 60)
                h, m = divmod(m, 60)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            print(f"[INFO] 文本已切分为 {len(sentences)} 句，开始逐句合成并接管时间戳...")


            for i, sentence in enumerate(sentences):
                # 字幕清理：去掉不需要在画面上显示的标点符号，让画面更干净
                clean_text = sentence.strip(" ，。、！？；：\"“”\n")
                if not clean_text: 
                    continue

                temp_audio_path = os.path.join(work_dir, f"segment_{i}.mp3")
                
                # 2. 逐句调用 edge-tts
                communicate = edge_tts.Communicate(
                    sentence, 
                    req.voice, 
                    # rate = "-12%", 
                    # pitch = "-6Hz",
                    # volume = "-5%"
                )
                await communicate.save(temp_audio_path)
                
                # 3. 读取片段，获取绝对精确的物理时长
                clip = AudioFileClip(temp_audio_path)
                audio_clips.append(clip)
                
                start_time_str = format_time(current_time)
                current_time += clip.duration
                end_time_str = format_time(current_time)
                
                # 4. 生成完美的【句级字幕】块
                srt_blocks.append(f"{srt_idx}\n{start_time_str} --> {end_time_str}\n{clean_text}\n")
                srt_idx += 1

            if not audio_clips:
                raise HTTPException(500, "文本解析异常，未能生成任何音频片段")

            # 5. 将所有音频小片段无缝拼接并保存为最终文件
            final_audio = concatenate_audioclips(audio_clips)
            final_audio.write_audiofile(audio_path, logger=None)
            
            final_srt = "\n".join(srt_blocks)
            print(f"[OK] 拼装完成，总音频时长: {final_audio.duration:.2f}s")
            print(f"[INFO] SRT({len(srt_blocks)}句)生成成功")
            
            final_audio.close()
        else:
            raise HTTPException(400, "必须提供 text")

        # 3️⃣ 加载音频
        audio = AudioFileClip(audio_path)
        duration = audio.duration
        print(f"[INFO] duration={duration:.2f}s")

        print(f"[INFO] 使用背景视频: {bg_video_path}")
        raw_bg_clip = VideoFileClip(bg_video_path)

        # import moviepy.video.fx as vfx
        if raw_bg_clip.duration < duration:
            print(f"[INFO] 素材视屏({raw_bg_clip.duration:.2f}s)比音频短，启动 vfx.loop 自动循环。")

            import math
            loop_count = math.ceil(duration / raw_bg_clip.duration)
            print(f"[INFO] 视频将首尾拼接 {loop_count} 次。")
            prepared_clip = (
                raw_bg_clip.resized((req.width, req.height))
                .without_audio()
            )

            from moviepy import concatenate_videoclips
            full_looped_clip = concatenate_videoclips([prepared_clip] * loop_count)
            bg_clip = full_looped_clip.with_duration(duration)
        else:
            print(f"[INFO] 素材视频长度充足，直接截取前 {duration:.2f} 秒")
            bg_clip = (
                raw_bg_clip.with_duration(duration)
                .resized((req.width, req.height))
                .without_audio()
            )

        subtitle_clips = []
        if final_srt:
            for start, end, text in parse_srt(final_srt):
                if start >= duration:
                    continue
                end = min(end, duration)

                subtitle_clips.append(
                    TextClip(
                        text=text,
                        font_size=req.fontsize,
                        color="white",
                        stroke_color="black",
                        stroke_width=2,
                        font=FONT_PATH,
                        size=(req.width - 120, None),
                        method="caption",
                        text_align="center",          # ✅ 文字内部居中
                        horizontal_align="center",    # ✅ 文字块水平居中
                        vertical_align="center",      # ✅ 文字块垂直居中
                        transparent=True,
                    )
                    .with_start(start)
                    .with_duration(end - start)
                    .with_position(("center", req.height - 430))
                )

        date_clips = []
        try:
            month_en, day_en, lunar_cn = get_date_elements()
            print(f"[INFO] 正在渲染胶片时间水印: {month_en} | {day_en} | {lunar_cn}")
            
            # 1. 英文月份层（居中靠上）
            date_clips.append(
                TextClip(
                    text=month_en,
                    font_size=100,             # 优雅大花体大字
                    color="white",
                    stroke_color="black",
                    stroke_width=2,
                    font=FONT_PATH,            # 如果有专属英文字体可以单独换
                    transparent=True,
                )
                .with_start(0)
                .with_duration(duration)
                .with_position(("center", req.height // 2 - 700)) # 位于正中间偏上
            )
            
            # 2. 巨大公历双位数字层（正居中）
            date_clips.append(
                TextClip(
                    text=day_en,
                    font_size=220,             # 冲击力巨大的数字
                    color="white",
                    stroke_color="black",
                    stroke_width=4,            # 粗黑边边框
                    font=FONT_PATH,
                    transparent=True,
                )
                .with_start(0)
                .with_duration(duration)
                .with_position(("center", req.height // 2 - 570)) # 牢牢钉在视频正中心
            )
            
            # 3. 农历地支层（居中靠下）
            date_clips.append(
                TextClip(
                    text=lunar_cn,
                    font_size=42,              # 优雅小字
                    color="white",
                    stroke_color="black",
                    stroke_width=1.5,
                    font=FONT_PATH,
                    transparent=True,
                )
                .with_start(0)
                .with_duration(duration)
                .with_position(("center", req.height // 2 - 340)) # 位于正中间偏下
            )
        except Exception as date_err:
            print(f"[WARNING] 时间水印渲染失败，跳过以确保视频完整生成: {date_err}")

        video = CompositeVideoClip([bg_clip] + date_clips + subtitle_clips).with_audio(audio)

        today = date.today().isoformat()
        filename = f"news_{today}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(OUTPUT_DIR, filename)

        video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            logger=None  # 关闭每秒刷新打印，让后台更干净
        )

        # 清理
        video.close()
        audio.close()
        bg_clip.close()
        raw_bg_clip.close()
        for c in audio_clips:
            c.close()

        print(f"[OK] 电影感短视频生成成功: {output_path}")

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
    finally:
        if work_dir and os.path.exists(work_dir):
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)


# ============ 健康检查 ============

@app.get("/health")
def health():
    return {"status": "ok", "output_dir": OUTPUT_DIR}
