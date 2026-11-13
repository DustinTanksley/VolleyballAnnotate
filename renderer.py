"""Render score annotations using timed transparent PNGs and FFmpeg."""
import argparse,math,subprocess,tempfile,json
from pathlib import Path
from project import load,calculate,sorted_events
from graphics import draw_score,title_card
from PIL import Image
from replays import add_replays, validate_replays
from encoding import FFMPEG, FFPROBE, select_encoder, video_args, ffmpeg_run

def build_overlay(project, folder, video_duration):
    """Always-visible score overlay for legacy whole-video export.

    One still image per score-change event, rather than hundreds of faded frames.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    base={'set':1,'points':{'home':0,'away':0},'sets':{'home':0,'away':0}}
    boundaries=[(0.,base)]
    for snapshot in calculate(project):
        time=float(snapshot['event']['time'])
        if 0<=time<video_duration:
            if snapshot['event']['type']=='set_end' and not snapshot['match_over']:
                newstate={**snapshot,'set':snapshot['set']+1,
                          'points':{'home':0,'away':0}}
            else:newstate=snapshot
            boundaries.append((time,newstate))
    boundaries.append((video_duration,None))
    concat=folder/'overlay.ffconcat'
    with concat.open('w',encoding='utf8') as f:
        f.write('ffconcat version 1.0\n')
        last=None
        for i,((time,state),(next_time,_)) in enumerate(zip(boundaries,boundaries[1:])):
            if next_time-time<=.00001:continue
            last=f'score_{i:04d}.png'
            draw_score(project,state).save(folder/last)
            f.write(f"file '{last}'\noption framerate 30\nduration {next_time-time:.6f}\n")
        if last:f.write(f"file '{last}'\noption framerate 30\n")
    return concat

def probe(path):
    info=json.loads(subprocess.check_output([FFPROBE,'-v','error','-show_format','-show_streams',
                                             '-of','json',str(path)],text=True))
    video=next((x for x in info['streams'] if x['codec_type']=='video'),None)
    if video is None:raise ValueError('No video stream found')
    return float(info['format']['duration']),int(video['width']),int(video['height']),any(x['codec_type']=='audio' for x in info['streams'])

def render(project,out,with_title=True,encoder_choice="auto"):
    encoder=select_encoder(encoder_choice)
    source=Path(project['video']).resolve()
    if not source.exists():raise FileNotFoundError(source)
    out=Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    video_duration,vid_w,vid_h,has_audio=probe(source)
    with tempfile.TemporaryDirectory(prefix='vb_overlay_') as folder:
        p=Path(folder);concat=build_overlay(project,p,video_duration)
        ov=project['overlay'];margin=int(ov.get('margin',28));position=ov.get('position','top_right')
        x=str(margin) if position.endswith('left') else f'main_w-overlay_w-{margin}'
        y=str(margin) if position.startswith('top') else f'main_h-overlay_h-{margin}'
        actual=p/'match.mp4'
        # The still-image overlay stream retains timing from concat durations.
        cmd=['-y','-i',str(source),'-safe','0','-f','concat','-i',str(concat),
             *([] if has_audio else ['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000']),
             '-filter_complex',f'[1:v]format=rgba[card];[0:v][card]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0:shortest=0[v]',
             '-map','[v]','-map','0:a:0' if has_audio else '2:a:0',
             '-r','30',*video_args(encoder),'-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
             '-t',str(video_duration),'-movflags','+faststart',str(actual)]
        ffmpeg_run(cmd, 'Scoreboard overlay', video_duration)
        # Insert optional slow-motion replay clips before adding the intro.
        if project.get('replays'):
            with_replays=p/'match_with_replays.mp4'
            add_replays(source,actual,with_replays,project,video_duration,vid_w,vid_h,p,encoder)
            actual=with_replays
        if with_title:
            card=p/'title.png';title_card(project,size=(vid_w,vid_h)).save(card)
            intro=p/'intro.mp4'
            # First 4 seconds title card + source has original audio uninterrupted after intro.
            title_cmd=['-y','-loop','1','-framerate','30','-t','4','-i',str(card),
                       '-f','lavfi','-t','4','-i','anullsrc=channel_layout=stereo:sample_rate=48000',
                       '-vf','format=yuv420p',*video_args(encoder),'-c:a','aac','-b:a','160k','-ar','48000','-ac','2','-video_track_timescale','30000','-shortest',str(intro)]
            ffmpeg_run(title_cmd, 'Title card', 4)
            # Use the concat FILTER, not packet-level concat demuxer: replay inserts
            # and title videos can have different time bases. Copy-concat may
            # otherwise stretch the final video despite correct audio duration.
            concat_cmd=['-y','-i',str(intro),'-i',str(actual),
                        '-filter_complex',
                        '[0:v]setpts=PTS-STARTPTS,fps=30[v0];'
                        '[1:v]setpts=PTS-STARTPTS,fps=30[v1];'
                        '[0:a]aresample=async=1:first_pts=0[a0];'
                        '[1:a]aresample=async=1:first_pts=0[a1];'
                        '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]',
                        '-map','[v]','-map','[a]',*video_args(encoder),'-pix_fmt','yuv420p',
                        '-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
                        '-movflags','+faststart',str(out)]
            ffmpeg_run(concat_cmd, 'Final assembly', video_duration + 4 + sum((r['end']-r['start'])/r['speed'] for r in project.get('replays',[])))
        else:
            actual.replace(out)
        print('Finished:',out,flush=True)



def defined_sets(project, duration):
    """User-selected source-video boundaries, not guessed from points or scoring rules."""
    result=[]
    for number, item in sorted(project.get('set_ranges', {}).items(), key=lambda x:int(x[0])):
        if 'start' not in item or 'end' not in item:continue
        a,b=float(item['start']),float(item['end'])
        if not 0 <= a < b <= duration + .05:
            raise ValueError(f'Set {number} has invalid IN/OUT markers')
        result.append((int(number),a,min(b,duration)))
    for (_,a,b),(_,c,d) in zip(result,result[1:]):
        if c < b - .01:raise ValueError('Set video ranges overlap; please edit the IN/OUT markers')
    return result


def build_persistent_overlay(project, folder, start, end, set_number):
    """One scoreboard PNG per scoring state, with no fading or per-frame PNG generation."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    snapshots=calculate(project)
    current={'set':set_number,'points':{'home':0,'away':0},'sets':{'home':0,'away':0}}
    points=[]
    for state in snapshots:
        event=state['event'];t=float(event['time'])
        if t <= start and state['set']==set_number and event['type']=='point':current=state
        elif start < t < end and state['set']==set_number and event['type']=='point':points.append((t,state))
    # Sets won prior to this set should be present even when the set begins with zero points.
    if not (isinstance(current,dict) and 'event' in current):
        earlier=[state for state in snapshots if float(state['event']['time']) <= start]
        if earlier:
            prior=earlier[-1]
            current={'set':set_number,'points':{'home':0,'away':0},'sets':prior['sets'].copy()}
    boundaries=[(start,current)]+points+[(end,None)]
    concat=folder/'persistent.ffconcat'
    with concat.open('w',encoding='utf8') as f:
        f.write('ffconcat version 1.0\n')
        for i,((t,state),(next_t,_)) in enumerate(zip(boundaries,boundaries[1:])):
            if next_t-t < .001:continue
            filename=f'set_score_{i:04d}.png'
            draw_score(project,state).save(folder/filename)
            f.write(f"file '{filename}'\noption framerate 30\nduration {next_t-t:.6f}\n")
        # End the concat at its final duration rather than leaving it open-ended.
        f.write(f"file '{filename}'\noption framerate 30\n")
    return concat


def render_set(project, out, number, encoder_choice='auto', with_title=False, output_height=0):
    """Render only marked source IN..OUT; scoreboard is visible and changes only on points.

    Project event/replay timestamps always refer to the original recording.
    """
    source=Path(project['video']).resolve()
    if not source.is_file():raise FileNotFoundError(source)
    video_duration,source_w,source_h,has_audio=probe(source)
    ranges={n:(a,b) for n,a,b in defined_sets(project,video_duration)}
    if number not in ranges:raise ValueError(f'Set {number} has no valid start and end markers')
    start,end=ranges[number];length=end-start
    if output_height and output_height>source_h:output_height=source_h
    if output_height:
        out_w=int(round(source_w*output_height/source_h/2)*2)
        out_h=int(output_height)//2*2
    else:out_w,out_h=source_w,source_h
    out=Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    encoder=select_encoder(encoder_choice)
    with tempfile.TemporaryDirectory(prefix='vb_set_') as tmp:
        folder=Path(tmp)
        playlist=build_persistent_overlay(project,folder,start,end,number)
        ov=project['overlay'];margin=int(ov.get('margin',28));position=ov.get('position','bottom_left')
        x=str(margin) if position.endswith('left') else f'main_w-overlay_w-{margin}'
        y=str(margin) if position.startswith('top') else f'main_h-overlay_h-{margin}'
        ordinary=folder/'set_base.mp4'
        # Single pass: seek/trim, overlay score updates, optionally downscale, encode.
        pre=f'[0:v]scale={out_w}:{out_h}:flags=fast_bilinear[scaled];' if output_height else ''
        base='[scaled]' if output_height else '[0:v]'
        graph=(pre+f'[1:v]format=rgba[card];{base}[card]overlay=x={x}:y={y}:'
               f'eof_action=repeat:repeatlast=1:shortest=0,fps=30,format=yuv420p[v]')
        cmd=['-y','-ss',f'{start:.6f}','-t',f'{length:.6f}','-i',str(source),
             '-safe','0','-f','concat','-i',str(playlist),
             *([] if has_audio else ['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000']),
             '-filter_complex',graph,'-map','[v]','-map','0:a:0' if has_audio else '2:a:0',
             *video_args(encoder),'-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','160k','-ar','48000','-ac','2',
             '-t',f'{length:.6f}','-video_track_timescale','30000',
             '-movflags','+faststart',str(ordinary)]
        ffmpeg_run(cmd,f'Set {number}: scoreboard',length)
        actual=ordinary
        # Replays are selected by insertion timestamp; their source positions stay absolute.
        selected=[r for r in validate_replays(project,video_duration)
                  if start <= float(r['insert']) < end]
        if selected:
            replay_project={**project,'replays':selected}
            modified=folder/'set_with_replays.mp4'
            add_replays(source,actual,modified,replay_project,length,out_w,out_h,folder,encoder,
                        source_size=(source_w,source_h),source_duration=video_duration,insert_offset=start)
            actual=modified
        if with_title:
            # Optional title: retained established safe concat-filter behavior.
            card=folder/'title.png';title_card(project,size=(out_w,out_h)).save(card)
            intro=folder/'intro.mp4'
            ffmpeg_run(['-y','-loop','1','-framerate','30','-t','4','-i',str(card),
                        '-f','lavfi','-t','4','-i','anullsrc=channel_layout=stereo:sample_rate=48000',
                        '-vf','format=yuv420p',*video_args(encoder),'-c:a','aac','-b:a','160k','-ar','48000','-ac','2','-video_track_timescale','30000','-shortest',str(intro)],
                       'Title card',4)
            # Add title using concat filter to avoid stream-timestamp incompatibility.
            # Matching 30 fps, H.264, AAC and timescale permits lossless packet-level join.
            # This avoids re-encoding the entire match simply to prepend 4 seconds.
            playlist=folder/'title_concat.ffconcat'
            with playlist.open('w',encoding='utf8') as f:
                f.write('ffconcat version 1.0\n')
                f.write(f"file '{intro.name}'\nfile '{actual.name}'\n")
            try:
                ffmpeg_run(['-y','-safe','0','-f','concat','-i',str(playlist),
                            '-c','copy','-movflags','+faststart',str(out)],'Title join (stream copy)')
                result_length,*_=probe(out)
                predicted=length+4+sum((r['end']-r['start'])/r['speed'] for r in selected)
                if abs(result_length-predicted)>1:
                    raise ValueError(f'Unexpected concatenation duration: {result_length:.2f}s versus {predicted:.2f}s')
            except (subprocess.CalledProcessError,ValueError) as exc:
                print('TITLE_COPY_FALLBACK|'+str(exc),flush=True)
                ffmpeg_run(['-y','-i',str(intro),'-i',str(actual),'-filter_complex',
                            '[0:v]setpts=PTS-STARTPTS,fps=30[v0];[1:v]setpts=PTS-STARTPTS,fps=30[v1];'
                            '[0:a]aresample=async=1:first_pts=0[a0];[1:a]aresample=async=1:first_pts=0[a1];'
                            '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]',
                            '-map','[v]','-map','[a]',*video_args(encoder),'-pix_fmt','yuv420p',
                            '-c:a','aac','-b:a','160k','-movflags','+faststart',str(out)],
                           'Final title assembly (fallback)',predicted)
        else:actual.replace(out)
    print('Finished:',out,flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('project');a.add_argument('output');a.add_argument('--no-title',action='store_true');a.add_argument('--set',type=int,default=0);a.add_argument('--height',type=int,choices=[0,720,1080,2160],default=0);a.add_argument('--encoder',choices=['auto','nvenc','cpu'],default='auto');args=a.parse_args()
    render_set(load(args.project),args.output,args.set,args.encoder,not args.no_title,args.height) if args.set else render(load(args.project),args.output,not args.no_title,args.encoder)
