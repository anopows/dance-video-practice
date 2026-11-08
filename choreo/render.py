"""Render the practice count strip beneath a video, preserving the full image.

Requires FFmpeg and Pillow. Analysis and settings are JSON sidecars, so count
phase can be revised without re-running beat detection. Speed is relative to
the input video. Audio uses offline Rubber Band R3 for pitch-preserving slowdown.
"""
from __future__ import annotations
import argparse, bisect, hashlib, json, math, subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from .slow_audio import decode_audio, stretch_wav
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent
STYLE=json.loads((ROOT/'static/guide-style.json').read_text())
WIDTH, HEIGHT, FPS=STYLE['width'],STYLE['height'],30
REGULAR='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
if not Path(REGULAR).exists():
    REGULAR='/System/Library/Fonts/Supplemental/Arial.ttf'
    BOLD='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
# Count strip palette, shared with the player canvas (choreo/static/app.js).
BG=(12,12,14);CELL=(24,24,28);ACTIVE=(244,242,236);MUTED=(143,142,150)
BRASS=(232,180,76);BRASS_ON_ACTIVE=(154,106,16)
PAD,GAP=6,8;CELL_W=(WIDTH-2*PAD-7*GAP)/8;CELL_TOP,CELL_BOTTOM=6,HEIGHT-6
BAR_HEIGHT={1:2,2:5,3:8};BAR_WIDTH={1:.44,2:.44,3:.62}

def accent(count):
    return 3 if count==8 else 2 if count==4 else 1 if count in (2,6) else 0

def at_time(beats,t,anchor,offset=0,grid=None):
    i=math.floor((t-offset-grid['origin']+1e-5)/grid['period']) if grid else bisect.bisect_right(beats,t-offset+1e-5)-1
    return i, None if i<0 else (i-anchor)%8+1

def settings_for(data,settings=None,timing='attack'):
    value=settings or {}
    if value.get('sourceSha256',data['sourceSha256'])!=data['sourceSha256']:
        raise ValueError('Settings belong to a different video')
    anchor=value.get('anchor',data['defaultAnchor']);offset=value.get('offset',0)
    legacy=bool(settings) and bool(data.get('grid')) and not value.get('gridId')
    source_beats=data['detectedBeats'] if legacy else data['beats']
    if not isinstance(anchor,int) or not (anchor in {e['index'] for e in data.get('attack',{}).get('events',[])} or 0<=anchor<len(source_beats)):
        raise ValueError('Anchor must be a valid beat index')
    if not isinstance(offset,(int,float)) or not math.isfinite(offset) or abs(offset)>.5:
        raise ValueError('Timing adjustment must be within ±0.5 seconds')
    if data.get('grid') and value.get('gridId') and value['gridId']!=data['grid']['id']:
        raise ValueError('Settings belong to a different tempo grid; review the starting 1 again')
    if legacy:
        if value.get('reviewed') is True:
            chosen_time=source_beats[anchor]+offset
            anchor=min(range(len(data['beats'])),key=lambda i:abs(data['beats'][i]-chosen_time))
            offset=chosen_time-data['beats'][anchor]
            if abs(offset)>.5:raise ValueError('Previous alignment is outside this grid')
        else:anchor=data['defaultAnchor']
    # An old steady-grid correction must not be applied a second time to a
    # detected attack. Keep the selected beat identity; offsets belong to modes.
    if settings and value.get('timingMode','steady')!=timing:offset=0
    if timing=='attack' and value.get('timingId') and value['timingId']!=data['attack']['id']:
        raise ValueError('Settings belong to different attack timing; review the alignment')
    return anchor,float(offset),bool(value.get('reviewed',False))

def event_at(data,t,anchor,offset=0,timing='attack'):
    if timing=='attack' and data.get('attack'):
        a=data['attack'];i=bisect.bisect_right(a['beats'],t-offset+1e-5)-1
        if i<0:return i,None,0,a['beats'][0]+offset,None
        e=a['events'][i];end=(a['beats'][i+1] if i+1<len(a['beats']) else data['duration'])+offset
        return i,(e['index']-anchor)%8+1,e['time']+offset,end,e['source']
    i,count=at_time(data['beats'],t,anchor,offset,data.get('grid'))
    start=data['grid']['origin']+i*data['grid']['period']+offset
    return i,count,start,start+data['grid']['period'],'pulse'

def attack_flash(lane,t,style=STYLE):
    """Same source-time envelope as PracticeCore.attackFlash; no count offset."""
    i=bisect.bisect_right(lane,t+1e-5,key=lambda event:event['time'])-1
    if i<0:return 0
    event=lane[i];age=max(0,t-event['time'])
    if age>=style['flashDurationSeconds']:return 0
    return (1-age/style['flashDurationSeconds'])*(style['minimumFlashGain']+style['strengthFlashGain']*event['strength'])

def guide_frame(data,t,anchor,offset=0,timing='attack',style=STYLE):
    return {'count':event_at(data,t,anchor,offset,timing)[1],
            'flashes':{key:attack_flash(data['attack']['lanes'][key],t,style) for key in style['instrumentOrder']}}

def make_strip(data,t,anchor,offset,speed,reviewed,timing='attack'):
    """One accent colour, three bar weights (2/6 thin, 4 medium, 8 thick),
    a dot marking the 1, and a high-contrast fill for the current count."""
    image=Image.new('RGB',(WIDTH,HEIGHT),BG);draw=ImageDraw.Draw(image)
    count=event_at(data,t,anchor,offset,timing)[1]
    for number in range(1,9):
        x=PAD+(number-1)*(CELL_W+GAP);level=accent(number);active=count==number
        draw.rounded_rectangle((round(x),CELL_TOP,round(x+CELL_W),CELL_BOTTOM),radius=12,fill=ACTIVE if active else CELL)
        ink=BG if active else ACTIVE if number==1 else MUTED
        draw.text((x+CELL_W/2,(CELL_TOP+CELL_BOTTOM)/2-3),str(number),font=FONTS[40],fill=ink,anchor='mm')
        if level:
            w=CELL_W*BAR_WIDTH[level];h=BAR_HEIGHT[level];bottom=CELL_BOTTOM-10
            draw.rounded_rectangle((round(x+(CELL_W-w)/2),bottom-h,round(x+(CELL_W+w)/2),bottom),radius=min(2,h/2),
                                   fill=BRASS_ON_ACTIVE if active else BRASS)
        if number==1:
            cx,cy=x+CELL_W/2,CELL_TOP+11
            draw.ellipse((cx-3.5,cy-3.5,cx+3.5,cy+3.5),fill=BRASS_ON_ACTIVE if active else BRASS)
    if count is None:
        image=Image.blend(Image.new('RGB',(WIDTH,HEIGHT),BG),image,.45)
    return image

FONTS={40:ImageFont.truetype(BOLD,40)}

def render(source,destination,data,*,speed=1,settings=None,timing='attack'):
    if not .25<=speed<=2:raise ValueError('Speed must be between 0.25 and 2')
    anchor,offset,reviewed=settings_for(data,settings,timing)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(source)]))
    duration=float(info['format']['duration']);count=math.ceil(duration/speed*FPS);total=count/FPS
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    partial=destination.with_suffix('.partial.mp4')
    # One pre-rendered soundtrack, shared with the practice player's engine.
    # Pitch preservation happens offline; no low-quality chained atempo filters.
    scratch=TemporaryDirectory(prefix='export-audio-',dir=destination.parent)
    audio_inputs=[];audio_label='0:a';audio_processing={'engine':'original','pitchPreserved':True}
    try:
        if speed!=1:
            original=Path(scratch.name)/'original.wav';stretched=Path(scratch.name)/'stretched.wav'
            decode_audio(source,original,duration)
            audio_processing=stretch_wav(original,stretched,speed,data.get('attack',{}).get('beats',[]))
            audio_inputs=['-i',str(stretched)];audio_label='2:a'
        filters=(f'[0:v]scale=720:-2,setsar=1,setpts=(PTS-STARTPTS)/{speed},fps={FPS}[v];'
                 f'[v][1:v]vstack=inputs=2[vout];[{audio_label}]asetpts=PTS-STARTPTS,apad=whole_dur={total}[aout]')
        command=['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(source),'-f','rawvideo','-pix_fmt','rgb24',
                 '-s',f'{WIDTH}x{HEIGHT}','-r',str(FPS),'-i','pipe:0',*audio_inputs,'-filter_complex',filters,
                 '-map','[vout]','-map','[aout]','-t',str(total),'-map_metadata','-1','-map_chapters','-1',
                 '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','4',
                 '-c:a','aac','-b:a','192k','-movflags','+faststart',str(partial)]
    except BaseException:
        scratch.cleanup();raise
    process=None
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
        for frame in range(count):
            # Nearest video frame: at most half an output frame of cue rounding.
            # This is display quantization, not a fixed acoustic attack offset.
            source_time=(frame+.5)/FPS*speed
            process.stdin.write(make_strip(data,source_time,anchor,offset,speed,reviewed,timing).tobytes())
            if frame%(FPS*20)==0:print(f'{destination.name}: {frame/FPS:.0f}/{total:.1f} seconds',flush=True)
        process.stdin.close()
        error=process.stderr.read().decode(errors='replace');code=process.wait()
        if code:raise RuntimeError(error)
        partial.replace(destination)
        rendered=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(destination)]))['streams'][0]
        manifest={'schema':1,'video':destination.name,'sourceSha256':data['sourceSha256'],
                  'timingId':data['attack']['id'] if timing=='attack' else data['grid']['id'],
                  'styleSha256':hashlib.sha256((ROOT/'static/guide-style.json').read_bytes()).hexdigest(),
                  'timingMode':timing,'anchor':anchor,'countOffsetSeconds':offset,'speed':speed,
                  'fps':FPS,'frameCount':count,'durationSeconds':total,'pictureSize':[rendered['width'],rendered['height']-HEIGHT],
                  'outputSize':[rendered['width'],rendered['height']],'countSequence':'1–8, continuous',
                  'instrumentVisualizations':False,'audioProcessing':audio_processing,
                  'frameSampling':'(frame + 0.5) / fps * speed'}
        destination.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print(f'Saved {destination}',flush=True)
    except BaseException:
        if process:
            process.kill();process.wait()
        partial.unlink(missing_ok=True);raise
    finally:
        scratch.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,default=ROOT/'source.mp4')
    parser.add_argument('--analysis',type=Path,default=ROOT/'practice-analysis.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--speed',type=float,default=1)
    parser.add_argument('--settings',type=Path)
    parser.add_argument('--timing',choices=['attack','steady'],default='attack')
    args=parser.parse_args()
    render(args.video,args.output,json.loads(args.analysis.read_text()),speed=args.speed,
           settings=json.loads(args.settings.read_text()) if args.settings else None,timing=args.timing)
