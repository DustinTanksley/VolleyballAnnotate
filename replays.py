"""Editable, non-destructive replay annotations and FFmpeg insertion."""
import math
import subprocess
from encoding import FFMPEG, video_args, ffmpeg_run
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def validate_replays(project, length):
    result = []
    for entry in project.get('replays', []):
        start, end = float(entry['start']), float(entry['end'])
        insert = float(entry.get('insert', end))
        zoom, speed = float(entry.get('zoom', 2)), float(entry.get('speed', .5))
        cx, cy = float(entry.get('cx', .5)), float(entry.get('cy', .5))
        if not (0 <= start < end <= length and 0 <= insert <= length):
            raise ValueError(f"Replay '{entry.get('id','?')}' has out-of-range timestamps")
        if not (.1 <= end-start <= 15 and 1 <= zoom <= 5 and .05 <= speed <= 1):
            raise ValueError('Replay must be 0.1–15 seconds; zoom 1–5×; playback speed 0.05–1×')
        if not (0 <= cx <= 1 and 0 <= cy <= 1):
            raise ValueError('Replay zoom center must be within the frame')
        result.append({**entry, 'start':start,'end':end,'insert':insert,'zoom':zoom,'speed':speed,'cx':cx,'cy':cy})
    return sorted(result, key=lambda r:(r['insert'],r.get('id','')))


def run(command, label, duration=None):
    ffmpeg_run(command, label, duration)


def replay_label(path, width, height):
    image=Image.new('RGBA',(width,height),(0,0,0,0))
    d=ImageDraw.Draw(image)
    fontsize=max(25,round(height*.041))
    font=None
    for candidate in ['C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']:
        try:font=ImageFont.truetype(candidate,fontsize);break
        except OSError:pass
    if font is None:font=ImageFont.load_default()
    text='INSTANT REPLAY';bbox=d.textbbox((0,0),text,font=font)
    x=max(15,round(width*.025));y=max(15,round(height*.03));pad=max(12,round(height*.014))
    d.rounded_rectangle((x,y,x+bbox[2]+2*pad,y+(bbox[3]-bbox[1])+2*pad),radius=12,fill=(11,35,58,218))
    d.text((x+pad,y+pad-bbox[1]),text,font=font,fill=(255,255,255,255))
    image.save(path)


def add_replays(source, original_render, target, project, video_duration, width, height, folder, encoder="cpu", source_size=None, source_duration=None, insert_offset=0.0):
    """Insert replays into a pre-rendered game at precise timestamps.

    We reencode the split sections for frame-accurate cuts, then concatenate
    normalized H264/AAC clips. This is slower but avoids keyframe seeking errors.
    """
    # Replay start/end positions refer to the ORIGINAL source recording.  The
    # insertion timestamp also starts in source coordinates, but must be
    # translated to the trimmed set clip's coordinate system for splitting.
    # Validate against source duration, not the shorter exported set.
    entries=validate_replays(project,source_duration if source_duration is not None else video_duration)
    entries=[{**r, 'insert':r['insert']-insert_offset} for r in entries]
    for r in entries:
        if not (0 <= r['insert'] <= video_duration + .001):
            raise ValueError(f"Replay '{r.get('id','?')}' inserts outside the exported set")
    source_width, source_height = source_size or (width, height)
    if not entries:
        Path(original_render).replace(target)
        return
    folder=Path(folder);label=folder/'replay_tag.png';replay_label(label,width,height)
    clips=[];cursor=0.;index=0
    def encode_regular(start,end):
        nonlocal index
        if end-start<.025:return
        output=folder/f'part_{index:04d}.mp4';index+=1
        run(['-y','-ss',f'{start:.6f}','-i',str(original_render),
             '-t',f'{end-start:.6f}','-map','0:v:0','-map','0:a:0',
             '-vf',f'scale={width}:{height}:flags=lanczos,fps=30,format=yuv420p',
             *video_args(encoder),
             '-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
             '-video_track_timescale','30000','-movflags','+faststart',str(output)], 'Regular clip', end-start)
        clips.append(output)
    for r in entries:
        insert=r['insert']
        if insert>cursor:encode_regular(cursor,insert)
        cursor=max(cursor,insert)
        duration=(r['end']-r['start'])/r['speed']
        crop_w=max(2,int(source_width/r['zoom'])//2*2)
        crop_h=max(2,int(source_height/r['zoom'])//2*2)
        x=max(0,min(source_width-crop_w,round(r['cx']*source_width-crop_w/2)))
        y=max(0,min(source_height-crop_h,round(r['cy']*source_height-crop_h/2)))
        output=folder/f'part_{index:04d}.mp4';index+=1
        graph=(f'[0:v]crop={crop_w}:{crop_h}:{x}:{y},'
               f'scale={width}:{height}:flags=lanczos,setpts=(PTS-STARTPTS)/{r["speed"]},'
               f'fps=30,format=yuv420p[zoom];[zoom][1:v]overlay=0:0:shortest=1[v]')
        run(['-y','-ss',f'{r["start"]:.6f}','-t',f'{r["end"]-r["start"]:.6f}',
             '-i',str(source),'-loop','1','-framerate','30','-i',str(label),
             '-f','lavfi','-t',f'{duration:.6f}','-i','anullsrc=channel_layout=stereo:sample_rate=48000',
             '-filter_complex',graph,'-map','[v]','-map','2:a:0',
             '-t',f'{duration:.6f}',*video_args(encoder),
             '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
             '-video_track_timescale','30000','-movflags','+faststart',str(output)], 'Instant replay', duration)
        clips.append(output)
    encode_regular(cursor,video_duration)
    listfile=folder/'replay_segments.ffconcat'
    with listfile.open('w',encoding='utf8') as f:
        f.write('ffconcat version 1.0\n')
        for clip in clips:
            f.write(f"file '{clip.name}'\n")
    run(['-y','-safe','0','-f','concat','-i',str(listfile),
         '-c','copy','-movflags','+faststart',str(target)], 'Joining replay segments')
