import shutil
import subprocess
import pytest
from choreo.media import inspect, normalize, solo_video, InvalidVideo


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_real_decode_and_reject_missing_audio_and_corruption(tmp_path):
    valid=tmp_path/'valid.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=320x240:r=24:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(valid)],check=True)
    info=inspect(valid)
    assert info['width']==320 and info['height']==240
    normalize(valid,tmp_path/'normalized.mp4',info)
    assert inspect(tmp_path/'normalized.mp4')['fps']==30
    silent=tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(valid),'-an','-c:v','copy',str(silent)],check=True)
    with pytest.raises(InvalidVideo,match='audio track'):
        inspect(silent)
    corrupt=tmp_path/'corrupt.mp4';corrupt.write_bytes(valid.read_bytes()[:100])
    with pytest.raises(InvalidVideo):
        inspect(corrupt)


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_audio_offset_is_preserved_in_playback(tmp_path):
    import array
    source=tmp_path/'offset.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x240:r=30:d=3',
        '-itsoffset','0.4','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-t','3',str(source)],check=True)
    normalize(source,tmp_path/'normalized.mp4',inspect(source))
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(tmp_path/'normalized.mp4'),'-ac','1','-ar','8000','-f','f32le','-'])
    samples=array.array('f');samples.frombytes(pcm)
    assert max(abs(x) for x in samples[:2000])<.001
    assert max(abs(x) for x in samples[4000:6000])>.01
    stem=tmp_path/'stem.wav';solo=tmp_path/'solo.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(tmp_path/'normalized.mp4'),'-vn',str(stem)],check=True)
    solo_video(tmp_path/'normalized.mp4',stem,solo,3)
    solo_pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(solo),'-ac','1','-ar','8000','-f','f32le','-'])
    solo_samples=array.array('f');solo_samples.frombytes(solo_pcm)
    assert abs(len(solo_samples)-len(samples))<=1024
    assert max(abs(x) for x in solo_samples[:2000])<.001
    assert max(abs(x) for x in solo_samples[4000:6000])>.01
    def video_packets(path):
        return subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','packet=pts_time,duration_time','-of','csv=p=0',str(path)])
    assert video_packets(solo)==video_packets(tmp_path/'normalized.mp4')
