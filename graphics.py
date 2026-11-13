from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import math,base64,io
FONT_PATHS=['C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
REG_PATHS=['C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
def font(n,bold=False):
    for p in (FONT_PATHS if bold else REG_PATHS):
        if Path(p).exists():return ImageFont.truetype(p,n)
    return ImageFont.load_default()
def load_logo(team,wh):
    try:
        data=team.get('logo_data')
        source=io.BytesIO(base64.b64decode(data)) if data else team.get('logo')
        if not source:return None
        im=Image.open(source).convert('RGBA');im.thumbnail((wh,wh),Image.Resampling.LANCZOS);return im
    except (OSError,ValueError,TypeError):return None

def draw_score(project,state,alpha=255):
    w=int(project['overlay'].get('width',550));w=max(360,min(1200,w));s=w/550
    h=int(196*s);im=Image.new('RGBA',(w,h),(0,0,0,0));d=ImageDraw.Draw(im)
    def xy(v):return int(v*s)
    d.rounded_rectangle((0,0,w-1,h-1),radius=xy(16),fill='#14283F',outline='#6B8299',width=max(1,xy(2)))
    d.rounded_rectangle((xy(10),xy(10),w-xy(10),xy(37)),radius=xy(7),fill='#254560')
    d.text((xy(19),xy(14)),f'SET {state["set"]}   •   PLAYOFFS',font=font(xy(14),True),fill='#E3F2FF')
    dots_end=3
    for idx,key in enumerate(('home','away')):
        y=xy(50+idx*67);team=project['teams'][key]; name=team.get('name',key)
        d.rounded_rectangle((xy(10),y, w-xy(10),y+xy(62)),radius=xy(9), fill='#1E3954' if idx==0 else '#21344A')
        logo=load_logo(team,xy(47));x=xy(18)
        if logo:im.alpha_composite(logo,(x+(xy(47)-logo.width)//2,y+(xy(47)-logo.height)//2))
        else:
            d.ellipse((x,y+xy(8),x+xy(42),y+xy(50)),fill='#407FA0');d.text((x+xy(11),y+xy(13)),name[:1].upper(),font=font(xy(25),True),fill='white')
        name_font=font(xy(21 if len(name)<15 else 17),True)
        d.text((xy(75),y+xy(7)),name[:22],font=name_font,fill='white')
        for k in range(dots_end):
            cx=xy(82+k*22); cy=y+xy(48)
            d.ellipse((cx-xy(6),cy-xy(6),cx+xy(6),cy+xy(6)),fill='#60DBA3' if k<state['sets'][key] else '#526A82')
        d.rounded_rectangle((w-xy(77),y+xy(6),w-xy(16),y+xy(56)),radius=xy(6),fill='#F2F6FC')
        score=str(state['points'][key]);bounds=d.textbbox((0,0),score,font=font(xy(33),True))
        d.text((w-xy(46)-(bounds[2]-bounds[0])/2,y+xy(9)),score,font=font(xy(33),True),fill='#14283F')
    if alpha!=255:im.putalpha(im.getchannel('A').point(lambda p:p*alpha//255))
    return im

def title_card(project, size=(1280,720)):
    w,h=size;im=Image.new('RGB',(w,h),'#12263E');d=ImageDraw.Draw(im);scale=w/1280
    def p(v):return round(v*scale)
    def center(txt,y,ft,col='#FFFFFF'):
        b=d.textbbox((0,0),txt,font=ft);d.text(((w-b[2]+b[0])//2,y),txt,font=ft,fill=col)
    center(project.get('match_title','PLAYOFFS').upper(),p(105),font(p(39),True),'#B5D3EE')
    center('VS',p(365),font(p(64),True))
    for x,key in [(w//4,'home'),(3*w//4,'away')]:
        team=project['teams'][key];logo=load_logo(team,p(175))
        if logo:im.paste(logo,(x-logo.width//2,p(230)),logo)
        else:d.ellipse((x-p(70),p(250),x+p(70),p(390)),fill='#2A587B')
        name=team.get('name',key); ft=font(p(31 if len(name)<16 else 24),True);b=d.textbbox((0,0),name,font=ft)
        d.text((x-(b[2]-b[0])//2,p(470)),name,font=ft,fill='white')
    center('BEST OF FIVE',p(605),font(p(23),False),'#9FBED9')
    return im
