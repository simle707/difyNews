
from pydantic import BaseModel

class VideoSegment(BaseModel):
    video_url: str
    start_time: float = 0.0
    end_time: float = -1.0

class MakeVideoRequest(BaseModel):
    text: str = ""
    audio_url: str = ""
    voice: str = "zh-CN-XiaoxiaoNeural"
    width: int = 1080
    height: int = 1920
    fontsize: int = 48
    srt: str = ""
    image_url: str = ""

# class MakeVideoRequest(BaseModel):
#     image_url: str = Field(..., description="背景图 URL")
#     audio_url: Optional[str] = Field(None, description="音频 URL (优先使用)")
#     text: Optional[str] = Field(None, description="如果没有音频URL，使用该文本进行 TTS")
#     voice: str = Field("zh-CN-XiaoxiaoNeural", description="TTS 音色")
#     srt: Optional[str] = Field(None, description="SRT 字幕文本")
#     width: int = Field(1080, description="视频宽度")
#     height: int = Field(1920, description="视频高度")
#     fontsize: int = Field(50, description="字幕字体大小")