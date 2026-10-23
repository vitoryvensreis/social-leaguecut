"""Safe VPS-only publication-media diagnostic.

Prints no file names, tokens, media URLs, file contents, or user text.
Examines the latest 3 uploaded files and checks publicly reachable responses.
"""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.meta_platform import MEDIA_DIR, PUBLIC_URL

def safe_head(url):
    request = Request(url, method="HEAD",
                      headers={"User-Agent": "facebookexternalhit/1.1"})
    try:
        with urlopen(request, timeout=9) as resp:
            return f"HTTP {resp.status} type={str(resp.headers.get('content-type') or '-')[:32]}"
    except HTTPError as exc:
        return f"HTTP {exc.code}"
    except (TimeoutError, URLError) as exc:
        return "ACCESS_ERROR: " + type(exc).__name__

def safe_range(url):
    request = Request(url, method="GET",
                      headers={"Range": "bytes=0-127",
                               "User-Agent": "facebookexternalhit/1.1"})
    try:
        with urlopen(request, timeout=9) as resp:
            resp.read(128)
            return f"HTTP {resp.status} type={str(resp.headers.get('content-type') or '-')[:32]} range={bool(resp.headers.get('content-range'))}"
    except HTTPError as exc:
        return f"HTTP {exc.code}"
    except (TimeoutError, URLError) as exc:
        return "ACCESS_ERROR: " + type(exc).__name__

def probe(path):
    if not shutil.which("ffprobe"):
        return "FFPROBE_MISSING"
    try:
        result=subprocess.run(
            ["ffprobe","-v","error","-show_entries",
             "stream=codec_name,codec_type,pix_fmt,width,height,avg_frame_rate,sample_rate,channels:format=duration,size",
             "-of","json",str(path)],
            check=False,capture_output=True,text=True,timeout=10,
        )
        if result.returncode:
            return "FFPROBE_ERROR"
        data=json.loads(result.stdout)
        info=[]
        for stream in data.get("streams",[]):
            if stream.get("codec_type")=="video":
                info.append("VIDEO=" + str(stream.get("codec_name")) +
                            " pixel=" + str(stream.get("pix_fmt")) +
                            " dimension=" + str(stream.get("width"))+"x"+str(stream.get("height")) +
                            " fps=" + str(stream.get("avg_frame_rate")))
            if stream.get("codec_type")=="audio":
                info.append("AUDIO=" + str(stream.get("codec_name")) +
                            " hz=" + str(stream.get("sample_rate")))
        duration=data.get("format",{}).get("duration")
        info.append("duration_s=" + str(round(float(duration),2)) if duration else "duration=unknown")
        return " ".join(info)
    except Exception as exc:
        return "PROBE_ERROR: "+type(exc).__name__

def main():
    if not MEDIA_DIR.exists():
        print("MEDIA_DIRECTORY_MISSING")
        return
    recent=sorted(MEDIA_DIR.glob("*.mp4"),key=lambda p:p.stat().st_mtime,reverse=True)[:3]
    print("RECENT_MP4_COUNT",len(recent),"FFPROBE_AVAILABLE",bool(shutil.which("ffprobe")))
    for index,path in enumerate(recent,1):
        print("MEDIA_INDEX",index,"BYTES",path.stat().st_size)
        print("LOCAL_CODEC",probe(path))
        # Public query is made with a URL assembled from an exact server-side
        # filename; neither that filename nor URL is ever written to logs.
        public=PUBLIC_URL+"/media/"+path.name
        local="http://127.0.0.1:8090/media/"+path.name
        print("LOCAL_HEAD",safe_head(local))
        print("PUBLIC_HEAD",safe_head(public))
        print("PUBLIC_RANGE",safe_range(public))

if __name__=="__main__":
    main()
