import json, os, tempfile, uuid, base64
from pathlib import Path

TEAMS = ['Local Net Work','Aces','Spikeaholics',"I'd Hit That",'Runcibilians']

def new_project(video=''):
    project = {'version':1,'video':str(video),'match_title':'Playoffs',
            'teams':{'home':{'name':'Local Net Work','logo':''},'away':{'name':'Aces','logo':''}},
            'court_sides':{'home':'left','away':'right'},
            'rules':{'best_of':5,'regular_target':25,'deciding_target':15,'win_by':2},
            'overlay':{'duration':15.0,'fade':1.0,'position':'bottom_left','width':550,'margin':28},
            'events':[], 'replays':[], 'set_ranges':{}}
    for team in project['teams'].values():load_default_logo(team)
    return project

def sorted_events(project):
    return sorted(project['events'],key=lambda e:(float(e['time']),int(e.get('sequence',0))))

def calculate(project):
    """Replay the timeline. Set winners are explicit, and no target-point rules are imposed."""
    points={'home':0,'away':0}; sets={'home':0,'away':0}; set_no=1
    snapshots=[];over=False
    best=int(project.get('rules',{}).get('best_of',5))
    for e in sorted_events(project):
        kind=e.get('type','point')
        if over:raise ValueError('Events after the match winner was declared; edit the sequence.')
        if kind=='point':
            team=e.get('team')
            if team not in points:raise ValueError('Point has invalid team')
            points[team]+=1
        elif kind=='set_end':
            winner=e.get('winner')
            if winner not in points:raise ValueError('Set end requires a selected winner')
            sets[winner]+=1
            over=sets[winner] >= best//2+1
        else:raise ValueError('Unknown event type: '+str(kind))
        snapshots.append({'event':e,'points':points.copy(),'sets':sets.copy(),
                          'set':set_no,'match_over':over})
        if kind=='set_end' and not over:
            set_no+=1;points={'home':0,'away':0}
    return snapshots

def state_after(project):
    snapshots=calculate(project)
    if not snapshots:return {'points':{'home':0,'away':0},'sets':{'home':0,'away':0},'set':1,'match_over':False}
    last=snapshots[-1]
    if last['event']['type']=='set_end' and not last['match_over']:
        return {**last,'set':last['set']+1,'points':{'home':0,'away':0}}
    return last

def attach_logo(team, path):
    """Embed PNG/JPEG bytes directly in the JSON; no external logo files required."""
    from io import BytesIO
    from PIL import Image
    with Image.open(path) as im:
        im=im.convert('RGBA');im.thumbnail((400,400),Image.Resampling.LANCZOS)
        b=BytesIO();im.save(b,format='PNG')
    team['logo_data']=base64.b64encode(b.getvalue()).decode('ascii')
    team['logo']=''

def default_logo_path(name):
    normalized={'Local Net Work':'local_net_work','Aces':'aces','Spikeaholics':'spikeaholics',
                "I'd Hit That":'id_hit_that','Runcibilians':'runcibilians'}
    slug=normalized.get(name)
    return Path(__file__).with_name('logos')/(slug+'.png') if slug else None

def load_default_logo(team):
    path=default_logo_path(team['name'])
    if path and path.exists():attach_logo(team,path)

def add_event(project,kind,team=None,time=0):
    event={'id':str(uuid.uuid4()),'time':round(max(0.,float(time)),3),'type':kind,'sequence':max([int(e.get('sequence',0)) for e in project['events']]+[0])+1}
    if kind=='point': event['team']=team
    elif kind=='set_end': event['winner']=team
    project['events'].append(event)
    return event

def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.stem, suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False); f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def load(path):
    with open(path,encoding='utf8') as f:data=json.load(f)
    if data.get('version')!=1: raise ValueError('Unsupported project format')
    if 'events' not in data or 'teams' not in data: raise ValueError('Invalid project')
    data.setdefault('replays', [])
    data.setdefault('set_ranges', {})
    for team in data['teams'].values():
        if not team.get('logo_data') and not team.get('logo'):
            load_default_logo(team)
    return data

def timestamp(t):
    t=max(0.,float(t)); h=int(t//3600); m=int(t%3600//60);s=t%60
    return f'{h:02d}:{m:02d}:{s:06.3f}'

def parse_timestamp(value):
    parts=value.strip().split(':')
    if len(parts)>3 or not parts:raise ValueError('Use HH:MM:SS.mmm')
    t=0.
    for x in parts:t=t*60+float(x)
    if t<0 or not (t<float('inf')):raise ValueError('Invalid timestamp')
    return t
