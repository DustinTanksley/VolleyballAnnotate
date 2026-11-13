"""Shared FFmpeg tools and video encoder settings for every export stage."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def executable(name):
    for local in (ROOT / (name + '.exe'), ROOT / name):
        if local.is_file():
            return str(local)
    return shutil.which(name) or name


FFMPEG = executable('ffmpeg')
FFPROBE = executable('ffprobe')


def nvenc_usable():
    """Run a real H.264 NVENC MP4 encode using the same arguments as the user test.

    Keep stderr for actionable diagnostics rather than silently treating every
    failure as missing hardware.
    """
    print(f'FFMPEG|{FFMPEG}', flush=True)
    with tempfile.TemporaryDirectory(prefix='vb_nvenc_') as folder:
        output = str(Path(folder) / 'nvenc_probe.mp4')
        cmd = [FFMPEG, '-hide_banner', '-loglevel', 'error',
               '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
               '-frames:v', '15', '-c:v', 'h264_nvenc',
               '-preset', 'p5', '-cq', '21', '-y', output]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'NVENC_TEST_ERROR|{type(exc).__name__}: {exc}', flush=True)
            return False
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or 'No encoder diagnostic output').strip()
            print('NVENC_TEST_ERROR|' + reason.replace('\n', ' | ')[:1800], flush=True)
            return False
        if not Path(output).is_file() or Path(output).stat().st_size == 0:
            print('NVENC_TEST_ERROR|FFmpeg returned success but no MP4 was created', flush=True)
            return False
    print('NVENC_TEST|Success', flush=True)
    return True


def select_encoder(requested='auto'):
    if requested not in ('auto', 'nvenc', 'cpu'):
        raise ValueError('Encoder must be auto, nvenc, or cpu')
    if requested == 'cpu':
        print('ENCODER|CPU libx264', flush=True)
        return 'cpu'
    if nvenc_usable():
        print('ENCODER|NVIDIA NVENC h264_nvenc', flush=True)
        return 'nvenc'
    if requested == 'nvenc':
        raise RuntimeError('NVENC probe failed. See NVENC_TEST_ERROR in the .render.log file. '
                           'Make sure the app and your PowerShell test use the same ffmpeg.exe.')
    print('ENCODER|NVENC test failed; falling back to CPU libx264 (see NVENC_TEST_ERROR)', flush=True)
    return 'cpu'


def video_args(encoder):
    if encoder == 'nvenc':
        return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', '21', '-b:v', '0']
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19']


def ffmpeg_run(args, label, duration=None):
    """Print one progress record per second for the GUI's export status."""
    print(f'STAGE|{label}|{duration if duration is not None else ""}', flush=True)
    cmd = [FFMPEG, '-hide_banner', '-nostats', '-stats_period', '1',
           '-progress', 'pipe:1', *map(str, args)]
    subprocess.run(cmd, check=True)
