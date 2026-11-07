"""Real audio/video checks for pitch, attack timing and a shared slow clock."""
import json
from fractions import Fraction
import shutil
import subprocess
import wave

import numpy as np
import pytest

from choreo.slow_audio import SAMPLE_RATE, decode_audio, probe, retime_video, stretch_wav


pytestmark = pytest.mark.skipif(not all(shutil.which(x) for x in ('ffmpeg', 'ffprobe', 'rubberband')),
                                reason='FFmpeg and Rubber Band CLI required')


@pytest.mark.parametrize('speed', [.25, .5, .75])
def test_pitch_attacks_and_video_timestamps_remain_synchronized(tmp_path, speed):
    sr=SAMPLE_RATE; duration=3; anchors=[.5, 1.25, 2, 2.75]
    t=np.arange(sr*duration)/sr
    signal=.03*np.sin(2*np.pi*440*t)
    for at in anchors:
        age=t-at
        signal+=np.where((age>=0)&(age<.08), .55*np.exp(-np.maximum(age,0)*40)*np.sin(2*np.pi*1600*age),0)
    audio=tmp_path/'source.wav'
    with wave.open(str(audio),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr)
        f.writeframes(np.repeat((signal*32767).astype('<i2'),2).tobytes())
    stretched=tmp_path/'slow.wav'
    report=stretch_wav(audio,stretched,speed,anchors)
    assert abs(report['outputFrames']-round(sr*duration/speed))<=2
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(stretched),'-ac','1','-f','f32le','-'])
    samples=np.frombuffer(pcm,dtype='<f4')
    # The opening sustained note should stay at 440 Hz, not fall with speed.
    segment=samples[round(.1/speed*sr):round(.4/speed*sr)]
    spectrum=abs(np.fft.rfft(segment*np.hanning(len(segment))))
    freq=np.fft.rfftfreq(len(segment),1/sr)[spectrum.argmax()]
    assert abs(freq-440)<4
    for at in anchors:
        expected=at/speed
        left=round((expected-.15/speed)*sr);right=round((expected+.15/speed)*sr)
        chunk=samples[left:right]
        peak=(left+np.argmax(abs(chunk)))/sr
        assert abs(peak*speed-at)<.04
    source=tmp_path/'source.mp4';target=tmp_path/'slow.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=160x120:r=30:d=3',
                    '-i',str(audio),'-c:v','libx264','-c:a','aac','-t','3',str(source)],check=True)
    retime_video(source,stretched,target,speed,duration)
    def timestamps(path):
        result=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                                        'packet=pts_time','-of','json',str(path)])
        return sorted(float(p['pts_time']) for p in json.loads(result)['packets'])
    # Stream-copy timestamp scaling rounds to the input container's time base.
    tick=float(Fraction(probe(source)['streams'][0]['time_base']))
    assert np.allclose(np.array(timestamps(target))*speed,timestamps(source),atol=tick+.000001,rtol=0)
    assert abs(float(probe(target)['format']['duration'])-duration/speed)<.04


def test_no_music_anchors_and_leading_silence(tmp_path):
    source=tmp_path/'input.wav';normalized=tmp_path/'normalized.wav';output=tmp_path/'output.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'sine=frequency=440:duration=1','-af','adelay=400,apad','-t','2',str(source)],check=True)
    decode_audio(source,normalized,2)
    result=stretch_wav(normalized,output,.25)
    assert result['outputFrames']==8*SAMPLE_RATE
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-ac','1','-f','f32le','-'])
    samples=np.frombuffer(pcm,dtype='<f4')
    assert max(abs(samples[:round(1.2*SAMPLE_RATE)]))<.001
    assert max(abs(samples[round(1.8*SAMPLE_RATE):round(2.3*SAMPLE_RATE)]))>.01
